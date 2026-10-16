"""Shared helpers for the Module 2 BMA labs (AgentCore Runtime environment).

Every lab script in this module imports this file to build its client and
resolve the IAM session role the same way, so the labs stay small and
consistent. It centralizes:

  * the Regions BMA supports during preview and the defaults we use,
  * the common CLI flags (``--region`` / ``--aws-profile`` / ``--role-arn``
    / ``--runtime``),
  * ``make_client`` — construct the OpenAI SDK client wired to BMA using a
    short-lived bearer token,
  * ``agentcore_environment`` — the environment dict that runs the agent on an
    AgentCore Runtime, and
  * ``resolve_session_role_arn`` — figure out which session role ARN to pass
    to BMA.

Module 2 differs from Module 1 (environment=none) in three ways:

  * ENVIRONMENT: the agent runs on an AgentCore Runtime (see
    ``agentcore_environment``) rather than ``{'type': 'none'}``.
  * AUTH: requests carry a bearer token minted by
    ``aws_bedrock_token_generator`` rather than SigV4-signed by the ``bedrock``
    provider.
  * SESSION ROLE: the default role is ``BmaSessionRole-<region>`` with an
    inline ``BmaSession`` policy that also allows the Runtime invoke actions.

BMA (powered by OpenAI) is in **public preview**: APIs, default models, and
Region availability can change. See the docs:

  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-bma.html
  https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai.html
  https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-sessions.html
"""

import argparse
import os
import sys

import boto3
from openai import OpenAI

# Regions where BMA is available during preview. We expose these as the
# allowed choices for --region so a typo fails fast instead of hitting an
# endpoint that does not exist.
SUPPORTED_REGIONS = ("us-east-1", "us-west-2", "us-east-2")

# Region used when --region is not given.
DEFAULT_REGION = "us-east-1"

# Default model BMA uses for inference. BMA is in public preview, so which
# model ids are available depends on your Region and account; override with
# --model if openai.gpt-5.6-luna is not enabled for you. (Model availability
# is region/account dependent — list what your endpoint exposes if unsure.)
DEFAULT_MODEL = "openai.gpt-5.6-luna"

# A simple, general-purpose system instruction used by the labs that just
# need the agent to answer. Labs that demonstrate a specific behavior define
# their own instructions locally instead.
DEFAULT_INSTRUCTIONS = (
    "You are a concise assistant. Answer the user's questions directly and "
    "accurately."
)

# Lifecycle events that mean the turn (or whole session) failed. When
# draining a turn we stop and raise on any of these instead of waiting
# forever for a completion that will never come.
FAILURE_EVENTS = {
    "agent.session.turn.failed",
    "agent.session.turn.cancelled",
    "agent.session.failed",
    "agent.session.environment.failed",
    "error",
}

# Prefix of the IAM session role that BMA assumes to do customer-authorized
# work (model inference plus invoking the AgentCore Runtime). The actual role
# name is region-scoped — BmaSessionRole-<region> — because the Runtime (and
# therefore the session) lives in one Region. The
# scripts/create_session_role.py helper creates a role with this name by
# default. Override at runtime with --role-arn or the BMA_INFERENCE_ROLE_ARN
# env var if yours differs.
DEFAULT_SESSION_ROLE_PREFIX = "BmaSessionRole"


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    """Add the AWS/BMA connection flags shared by every lab script."""
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        choices=SUPPORTED_REGIONS,
        help=f"AWS Region to call BMA in (default: {DEFAULT_REGION}).",
    )
    parser.add_argument(
        "--aws-profile",
        default=os.environ.get("AWS_PROFILE"),
        help=(
            "Named profile from ~/.aws/config|credentials to sign requests "
            "with (default: $AWS_PROFILE, or the AWS default credential chain "
            "if unset). Use this if your 'default' profile has stale or invalid "
            "credentials (e.g. an expired SSO session or rotated static keys), "
            "which otherwise shows up as a 401 AuthenticationError."
        ),
    )


def add_role_arn_arg(parser: argparse.ArgumentParser) -> None:
    """Add the --role-arn flag (only needed for session-creating calls)."""
    parser.add_argument(
        "--role-arn",
        default=os.environ.get("BMA_INFERENCE_ROLE_ARN"),
        help=(
            "ARN of the IAM session role BMA assumes for inference. Required "
            f"for this preview. Default: ${{BMA_INFERENCE_ROLE_ARN}}, or "
            f"'arn:aws:iam::<account-id>:role/{DEFAULT_SESSION_ROLE_PREFIX}-<region>' "
            "using the Runtime ARN's Region and the calling identity's account "
            "id if neither is set."
        ),
    )


def add_runtime_arg(parser: argparse.ArgumentParser) -> None:
    """Add the --runtime flag naming the AgentCore Runtime to run the agent on.

    The Runtime ARN also carries the Region and account id the labs derive the
    session Region and the default session role from, so a session always runs
    in the Runtime's own Region.
    """
    parser.add_argument(
        "--runtime",
        default=os.environ.get("BMA_RUNTIME_ARN"),
        help=(
            "ARN of the AgentCore Runtime to run the agent on, e.g. "
            "arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<runtime-id>. "
            "Default: $BMA_RUNTIME_ARN. The session runs in the Runtime's own "
            "Region (derived from this ARN)."
        ),
    )


def connection_flags(args: argparse.Namespace) -> str:
    """Reconstruct the connection flags the user ran with, as a CLI suffix.

    Sessions are REGION-SCOPED and tied to a specific AgentCore Runtime: a
    follow-up command (continue, inspect, delete) must target the SAME Runtime
    (and profile) the session was created on, or the service returns a 404 "no
    managed agent session found" because it looks in the wrong place. The lab
    scripts print follow-up commands, so they append this suffix to make those
    commands copy-paste correct.

    Returns e.g. " --runtime arn:... --aws-profile bma-test" (leading space),
    or "" when only defaults were used. Only non-default values are emitted:
    --region when it differs from DEFAULT_REGION, --runtime when one was given,
    and --aws-profile when one was given (explicitly or via $AWS_PROFILE). The
    Runtime ARN already implies the Region, so when --runtime is present the
    Region is carried by it.
    """
    parts = []
    region = getattr(args, "region", None)
    if region and region != DEFAULT_REGION:
        parts.append(f"--region {region}")
    runtime = getattr(args, "runtime", None)
    if runtime:
        parts.append(f"--runtime {runtime}")
    profile = getattr(args, "aws_profile", None)
    if profile:
        parts.append(f"--aws-profile {profile}")
    return (" " + " ".join(parts)) if parts else ""


class _ProfileCredentialsProvider:
    """Adapter so ``provide_token`` mints the token under a chosen profile.

    ``provide_token`` accepts an ``aws_credentials_provider`` and calls
    ``.load()`` on it to get botocore ``Credentials``; without one it falls
    back to the ambient default chain. We wrap a profile-bound
    ``boto3.Session`` so the bearer token is minted as the SAME identity that
    ``--aws-profile`` selects for the STS/IAM calls — not whatever the ambient
    chain happens to resolve. ``.load()`` is resolved lazily (on first token
    mint) and the credentials object refreshes itself as the SDK re-mints.
    """

    def __init__(self, aws_profile: str | None):
        self._aws_profile = aws_profile

    def load(self):
        import boto3  # noqa: E402 — lazy; only needed on an actual client build

        creds = boto3.Session(profile_name=self._aws_profile).get_credentials()
        if creds is None:
            raise RuntimeError(
                "No AWS credentials found for "
                + (f"profile {self._aws_profile!r}" if self._aws_profile
                   else "the default credential chain")
                + ". Set --aws-profile / AWS_PROFILE or configure credentials."
            )
        return creds


def make_client(region: str, aws_profile: str | None = None) -> OpenAI:
    """Build an OpenAI client wired to BMA using a short-lived bearer token.

    Module 2 authenticates with a bearer token minted by
    ``aws_bedrock_token_generator.provide_token`` rather than SigV4-signing
    each request (Module 1's ``bedrock`` provider). There is still no OpenAI
    API key.

    The token is minted under the SAME identity ``--aws-profile`` selects: we
    pass ``provide_token`` an ``aws_credentials_provider`` bound to a
    profile-scoped ``boto3.Session``, instead of letting it fall back to the
    ambient default chain. This keeps the token identity consistent with the
    role-resolution / preflight STS calls (which also honor the profile) — so a
    profile that differs from the ambient ``AWS_PROFILE`` can't silently mint
    the token as the wrong account. When no profile is given, the behavior is
    the ordinary default chain. Passing ``api_key`` a callable lets the SDK
    re-mint a fresh token when the previous one expires.

    ``provide_token`` is imported lazily, INSIDE this function, so that
    ``py_compile`` and ``--help`` work even when aws-bedrock-token-generator is
    not installed; only an actual client build requires the package.
    """
    from aws_bedrock_token_generator import provide_token  # noqa: E402 — lazy import

    provider = _ProfileCredentialsProvider(aws_profile)
    return OpenAI(
        api_key=lambda: provide_token(region=region, aws_credentials_provider=provider),
        base_url=f"https://bedrock-mantle.{region}.api.aws/openai/v1",
    )


def region_from_runtime_arn(runtime_arn: str) -> str | None:
    """Extract the Region from an AgentCore Runtime ARN.

    ARN shape: arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<id>.
    Returns None if the ARN is malformed (too few colon-delimited fields or an
    empty Region field), so callers can fail cleanly rather than crash.
    """
    parts = runtime_arn.split(":")
    if len(parts) >= 4 and parts[3]:
        return parts[3]
    return None


def account_from_runtime_arn(runtime_arn: str) -> str | None:
    """Extract the account id from an AgentCore Runtime ARN.

    ARN shape: arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<id>.
    Returns None if the ARN is malformed (too few colon-delimited fields or an
    empty account field).
    """
    parts = runtime_arn.split(":")
    if len(parts) >= 5 and parts[4]:
        return parts[4]
    return None


def resolve_session_role_arn(
    explicit_role_arn: str | None,
    runtime_arn: str | None,
    aws_profile: str | None,
) -> str:
    """Resolve the session role ARN BMA assumes for inference and Runtime calls.

    Precedence: an explicit --role-arn / BMA_INFERENCE_ROLE_ARN wins; otherwise
    we build the region-scoped default name BmaSessionRole-<region> — where the
    Region comes from the Runtime ARN and the account id from STS
    get-caller-identity — in the caller's own account.
    """
    if explicit_role_arn:
        return explicit_role_arn

    region = region_from_runtime_arn(runtime_arn) if runtime_arn else None
    if not region:
        raise ValueError(
            "Cannot build the default session role ARN without a Region. Pass "
            "--role-arn/BMA_INFERENCE_ROLE_ARN, or a well-formed --runtime ARN "
            "(arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<id>) so "
            "the Region can be derived."
        )

    session = boto3.Session(profile_name=aws_profile)
    account_id = session.client("sts").get_caller_identity()["Account"]
    role_arn = f"arn:aws:iam::{account_id}:role/{DEFAULT_SESSION_ROLE_PREFIX}-{region}"
    print(
        f"No --role-arn given; defaulting to {role_arn} "
        "(override with --role-arn or BMA_INFERENCE_ROLE_ARN if this role "
        "does not exist or isn't the one you want BMA to assume).",
        file=sys.stderr,
    )
    return role_arn


def agentcore_environment(runtime_arn: str) -> dict:
    """Build the environment dict that runs the agent on an AgentCore Runtime.

    Passed as ``environment=`` to the session-create call. ``runtime_qualifier``
    targets the Runtime's DEFAULT endpoint; ``workspace_directory`` is where the
    agent does its work and ``capability_directories`` are the mounted plugin
    paths. These defaults mirror the AgentCore Runtime get-started tutorial;
    later labs can mount Skills / local paths by extending them.
    """
    return {
        "type": "aws_bedrock_agentcore",
        "runtime_arn": runtime_arn,
        "runtime_qualifier": "DEFAULT",
        "workspace_directory": "/home/app/workspace",
        "capability_directories": ["/opt/bma/plugins"],
    }


def drain_turn(client: OpenAI, session_id: str, print_output: bool = True) -> None:
    """Stream the in-flight turn's events until it completes.

    A "turn" is one request/response cycle on a session. After a turn starts
    (by creating the session with an initial ``input``, or by submitting a
    message to the events endpoint), the agent emits a stream of events:
    reasoning, output-text deltas, the final output-text, and finally a
    turn-completed event. This opens the event stream and consumes it until
    the turn is done:

      * when ``print_output`` is True, print the final assistant text when we
        see ``...turn.output_text.done`` (its ``to_json()`` shows role,
        content, item id, etc.),
      * stop on ``...turn.completed`` / ``...session.completed``,
      * raise on any ``FAILURE_EVENTS`` so a failed turn is loud, not silent.

    Lab 02 passes ``print_output=False`` because it inspects the session's
    durable items afterward rather than echoing the streamed text.
    """
    with client.beta.agents.sessions.events.stream(session_id) as events:
        for event in events:
            if event.type in FAILURE_EVENTS:
                raise RuntimeError(f"Agent lifecycle failure: {event.type}")
            if print_output and event.type == "agent.session.turn.output_text.done":
                print(event.to_json())
            if event.type in {"agent.session.turn.completed", "agent.session.completed"}:
                break
