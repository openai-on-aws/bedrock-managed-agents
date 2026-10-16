#!/usr/bin/env python3
"""create_session_role.py — create the IAM session role that BMA (Bedrock
Managed Agents, powered by OpenAI) assumes for Module 2 (AgentCore Runtime).

BMA assumes this role (its trust policy trusts bedrock-mantle.amazonaws.com)
to work on your behalf. The role carries an inline policy named BmaSession
that allows (1) bedrock-mantle:CreateInference for a specific model and (2) the
AgentCore Runtime invoke actions (bedrock-agentcore:InvokeAgentRuntime /
StopRuntimeSession) scoped to the Runtime you name with --runtime. This script
creates that role and its inline policy.

The role name is region-scoped — BmaSessionRole-<region> — because a session
runs on one AgentCore Runtime, which lives in one Region. The Region (and the
account id, for cross-checking) are derived from the --runtime ARN.

It also PRINTS (but does NOT create or attach) two policies you need on your
own calling identity — the identity that invokes the BMA session APIs:
  * an iam:PassRole policy so you may pass this role to BMA, and
  * a BMA-client policy granting the session actions plus
    bedrock-mantle:CallWithBearerToken (Module 2 authenticates with a bearer
    token).
Review those and attach them to your user/role yourself.

This replaces the old Bash role-creation helper and runs natively on macOS,
Linux and Windows. It uses boto3 instead of the AWS CLI, so run it with the module
venv active (i.e. after `python setup.py`); it does NOT require the aws CLI.

============================================================================
LEAST PRIVILEGE — READ THIS
----------------------------------------------------------------------------
The policies below are a STARTING POINT scoped for a learning sandbox, not a
finished production configuration. Before using this beyond a sandbox you
MUST review and narrow them, in particular:
  * the inference statement KEEPS Resource "*" on purpose — do NOT try to
    scope it to a model-specific ARN. bedrock-mantle:CreateInference does not
    take a model-specific resource ARN; the bedrock-mantle:Model condition
    below is what restricts which model may be invoked, so narrow the model
    THERE, not via Resource;
  * the bedrock-agentcore statement is already scoped to the one Runtime (and
    its endpoints) you passed via --runtime — for production, pin it further
    to a specific runtime-endpoint qualifier instead of the "/*" wildcard, and
    grant only the actions you actually use;
  * the model condition (bedrock-mantle:Model) pins ONE model — confirm it is
    the model you intend to allow, and add others deliberately;
  * the trust policy is scoped to your account via aws:SourceAccount and
    aws:SourceArn — keep it scoped; do not broaden the principal.
Always VERIFY the effective permissions (e.g. with the IAM policy simulator)
before deploying. See the security and prerequisites docs:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-bma.html
  https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-prerequisites.html
  https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-security.html
============================================================================

Usage:
  python create_session_role.py --runtime <runtime-arn>              # create (prompts)
  python create_session_role.py --runtime <runtime-arn> --yes        # create without prompting
  python create_session_role.py --runtime <runtime-arn> --dry-run    # print policy JSON; no AWS calls
  python create_session_role.py --runtime <runtime-arn> --role-name NAME --model MODEL

Options / environment overrides:
  --runtime ARN      REQUIRED. AgentCore Runtime ARN the session runs on; its
                     Region names the role (BmaSessionRole-<region>) and its
                     account id is cross-checked against your identity.
  --role-name NAME   role to create (default: $ROLE_NAME or
                     BmaSessionRole-<region> derived from --runtime)
  --model MODEL      model allowed by the inference policy (default: $MODEL or
                     openai.gpt-5.6-luna)
  --aws-profile P    AWS profile used for AWS calls (default: $AWS_PROFILE)
  --yes              skip the confirmation prompt
  --dry-run          print the policy JSONs and exit 0 without any AWS calls
"""

import argparse
import json
import os
import sys

import boto3

# Default prefix of the role we create; the final name is region-scoped as
# BmaSessionRole-<region>. Overridable via --role-name or ROLE_NAME.
DEFAULT_ROLE_PREFIX = "BmaSessionRole"
# Default model the inference policy allows. Overridable via --model or MODEL.
DEFAULT_MODEL = "openai.gpt-5.6-luna"
# Name of the inline policy we put on the role. Fixed (not derived from the
# role name) so every Module 2 role carries a consistently named BmaSession
# policy that the labs/preflight look for.
INLINE_POLICY_NAME = "BmaSession"
# Placeholders used only for --dry-run output (no AWS call made).
PLACEHOLDER_ACCOUNT = "123456789012"
PLACEHOLDER_REGION = "us-east-1"
PLACEHOLDER_RUNTIME_ID = "my-runtime-id"


import re  # noqa: E402 — used by the strict ARN validator below

# Strict AgentCore Runtime ARN grammar. We validate before putting the ARN
# into an IAM Resource, so a wildcard, a typo, or a cross-service value cannot
# silently widen the generated policy:
#   arn:<partition>:bedrock-agentcore:<region>:<12-digit-account>:runtime/<id>
# - partition: aws, aws-us-gov, aws-cn (letters/hyphens)
# - region:    lowercase letters, digits, hyphens (e.g. us-east-1)
# - account:   exactly 12 digits
# - runtime id: no "*" or "?" (reject wildcards), no ":" or whitespace
_RUNTIME_ARN_RE = re.compile(
    r"^arn:(?P<partition>aws[a-z0-9-]*):bedrock-agentcore:"
    r"(?P<region>[a-z0-9-]+):(?P<account>\d{12}):runtime/(?P<id>[^:\s*?/]+)$"
)


def _runtime_arn_match(runtime_arn: str):
    """Return the regex match for a well-formed Runtime ARN, else None.

    Rejects wildcards (``*``/``?``), non-12-digit accounts, the wrong service,
    and malformed shapes — so these never reach the IAM policy's Resource.
    """
    if not isinstance(runtime_arn, str):
        return None
    return _RUNTIME_ARN_RE.match(runtime_arn.strip())


def _runtime_region(runtime_arn: str) -> str | None:
    """Region of a well-formed AgentCore Runtime ARN, or None if malformed."""
    m = _runtime_arn_match(runtime_arn)
    return m.group("region") if m else None


def _runtime_account(runtime_arn: str) -> str | None:
    """Account of a well-formed AgentCore Runtime ARN, or None if malformed."""
    m = _runtime_arn_match(runtime_arn)
    return m.group("account") if m else None


def _runtime_endpoint_arn(runtime_arn: str) -> str:
    """Derive the runtime-endpoint ARN wildcard from a Runtime ARN.

    StopRuntimeSession with a qualifier authorizes against the endpoint ARN
    arn:...:runtime/<id>/runtime-endpoint/*, so the inline policy must list it
    alongside the runtime ARN itself.
    """
    return f"{runtime_arn}/runtime-endpoint/*"


# ---- policy document builders ----------------------------------------------
# Each returns a Python dict so the same object can be json.dumps'd for display
# (dry-run) or serialized and handed to boto3 for a real create/put. All take
# the account id (and, where needed, the Runtime ARN) as arguments so the same
# code path is used for the real values and the dry-run placeholders.

def trust_policy(account: str, region: str) -> dict:
    """Trust policy attached to the session role.

    Lets bedrock-mantle.amazonaws.com assume the role, scoped to THIS account
    via aws:SourceAccount and aws:SourceArn so no other account can trigger it.
    (``region`` is accepted for signature symmetry with the rest of the
    builders; the SourceArn stays wildcarded on Region so one role works across
    the account's bedrock-mantle usage.)
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-mantle.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-mantle:*:{account}:*"},
                },
            }
        ],
    }


def session_policy(model: str, runtime_arn: str) -> dict:
    """Inline BmaSession policy attached to the session role.

    Two statements:
      1. bedrock-mantle:CreateInference — keeps Resource "*" on purpose:
         CreateInference takes no model-specific ARN. The bedrock-mantle:Model
         condition is what pins the model, so narrow the model there — not via
         Resource.
      2. bedrock-agentcore:InvokeAgentRuntime / StopRuntimeSession — scoped to
         the specific Runtime ARN AND its endpoint ARN
         (runtime/<id>/runtime-endpoint/*), because StopRuntimeSession with a
         qualifier authorizes against the endpoint ARN.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "bedrock-mantle:CreateInference",
                "Resource": "*",
                "Condition": {"StringEquals": {"bedrock-mantle:Model": model}},
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:InvokeAgentRuntime",
                    "bedrock-agentcore:StopRuntimeSession",
                ],
                "Resource": [
                    runtime_arn,
                    _runtime_endpoint_arn(runtime_arn),
                ],
            },
        ],
    }


def passrole_policy(account: str, role_name: str) -> dict:
    """iam:PassRole policy for your OWN identity (printed, not attached).

    Lets your calling identity pass this specific session role to BMA, scoped
    via iam:PassedToService so it can only be passed to bedrock-mantle.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "iam:PassRole",
                "Resource": f"arn:aws:iam::{account}:role/{role_name}",
                "Condition": {
                    "StringEquals": {"iam:PassedToService": "bedrock-mantle.amazonaws.com"}
                },
            }
        ],
    }


def bma_client_policy() -> dict:
    """BMA-client policy for your OWN identity (printed, not attached).

    Grants the session actions your identity needs to drive BMA sessions, plus
    bedrock-mantle:CallWithBearerToken — Module 2 authenticates with a bearer
    token (see bma_common.make_client), so the caller must be allowed to make
    that bearer-token call.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-mantle:CallWithBearerToken",
                    "bedrock-mantle:CreateAgentSession",
                    "bedrock-mantle:GetAgentSession",
                    "bedrock-mantle:ListAgentSessions",
                    "bedrock-mantle:CreateAgentSessionEvent",
                    "bedrock-mantle:ListAgentSessionEvents",
                    "bedrock-mantle:ListAgentSessionItems",
                    "bedrock-mantle:DeleteAgentSession",
                ],
                "Resource": "*",
            }
        ],
    }


def _dumps(doc: dict) -> str:
    """JSON-serialize a policy document for human display."""
    return json.dumps(doc, indent=2)


# Print the two policies the CALLER must attach to its own identity. Printed in
# both dry-run and real runs because this script never creates them for you.
# Everything here goes to STDERR: stdout is reserved for the final role ARN.
def print_caller_policies(account: str, role_name: str) -> None:
    print(
        "\n"
        "----------------------------------------------------------------------------\n"
        "ATTACH THESE TO YOUR OWN CALLING IDENTITY (user/role that invokes BMA).\n"
        "This script does NOT create or attach them — review and attach manually.\n"
        "\n"
        "1) iam:PassRole — lets you pass the session role to BMA:",
        file=sys.stderr,
    )
    print(_dumps(passrole_policy(account, role_name)), file=sys.stderr)
    print(
        "\n"
        "2) BMA-client policy — the session actions your identity needs,\n"
        "   including bedrock-mantle:CallWithBearerToken for Module 2's bearer-token auth:",
        file=sys.stderr,
    )
    print(_dumps(bma_client_policy()), file=sys.stderr)
    print(
        "\n"
        'Reminder: these are a least-privilege STARTING POINT. Review, narrow (the\n'
        'BMA-client Resource "*" in particular), and VERIFY before production.\n'
        "----------------------------------------------------------------------------",
        file=sys.stderr,
    )


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create the IAM session role BMA assumes for Module 2 (AgentCore "
            "Runtime), plus its inline BmaSession policy. Also PRINTS (not "
            "attaches) the two caller-side policies you must add to your own "
            "identity."
        )
    )
    parser.add_argument(
        "--runtime",
        default=os.environ.get("BMA_RUNTIME_ARN"),
        help=(
            "REQUIRED. AgentCore Runtime ARN the session runs on, e.g. "
            "arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<id>. Its "
            "Region names the role (BmaSessionRole-<region>) and the inline "
            "policy is scoped to this Runtime. Default: $BMA_RUNTIME_ARN."
        ),
    )
    parser.add_argument(
        "--role-name",
        default=os.environ.get("ROLE_NAME"),
        help=(
            "role to create (default: $ROLE_NAME or BmaSessionRole-<region> "
            "derived from --runtime)."
        ),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MODEL", DEFAULT_MODEL),
        help="model allowed by the inference policy (default: $MODEL or openai.gpt-5.6-luna).",
    )
    parser.add_argument(
        "--aws-profile",
        default=os.environ.get("AWS_PROFILE"),
        help="AWS profile used for AWS calls (default: $AWS_PROFILE).",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="skip the confirmation prompt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the policy JSONs and exit 0 without any AWS calls.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    model = args.model

    # ---- dry-run: print everything, make no AWS calls, exit 0 --------------
    # Checked BEFORE any boto3 Session/client is built, so no credentials are
    # read and zero network calls happen in this branch. When --runtime is a
    # placeholder (or omitted) we fall back to placeholder region/account/id so
    # the JSON is still well-formed and illustrative.
    if args.dry_run:
        runtime_arn = args.runtime
        # If a --runtime was GIVEN, it must be well-formed even in dry-run —
        # otherwise we'd silently paper over a wildcard/typo with placeholders
        # and show a policy that doesn't match what a real run would reject.
        # With NO --runtime, fall back to placeholders for an illustrative preview.
        if runtime_arn and not _runtime_arn_match(runtime_arn):
            print(
                f"Malformed --runtime ARN {runtime_arn!r}: expected "
                "arn:<partition>:bedrock-agentcore:<region>:<12-digit-account>:runtime/<id> "
                "(no wildcards). Omit --runtime to preview with placeholders.",
                file=sys.stderr,
            )
            return 1
        region = (_runtime_region(runtime_arn) if runtime_arn else None) or PLACEHOLDER_REGION
        account = (_runtime_account(runtime_arn) if runtime_arn else None) or PLACEHOLDER_ACCOUNT
        if not runtime_arn:
            runtime_arn = (
                f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{PLACEHOLDER_RUNTIME_ID}"
            )
        role_name = args.role_name or f"{DEFAULT_ROLE_PREFIX}-{region}"

        print("DRY RUN — no AWS calls will be made and nothing will be created.", file=sys.stderr)
        print(f"Using placeholder values for display only where real ones are absent.", file=sys.stderr)
        print(f"Role name: {role_name}", file=sys.stderr)
        print(f"Model:     {model}", file=sys.stderr)
        print(f"Runtime:   {runtime_arn}", file=sys.stderr)

        print("", file=sys.stderr)
        print("Trust policy (attached to the session role):", file=sys.stderr)
        print(_dumps(trust_policy(account, region)), file=sys.stderr)

        print("", file=sys.stderr)
        print(
            f"Inline policy {INLINE_POLICY_NAME} (attached to the session role):",
            file=sys.stderr,
        )
        print(_dumps(session_policy(model, runtime_arn)), file=sys.stderr)

        print_caller_policies(account, role_name)

        print("", file=sys.stderr)
        print("Dry run complete. Re-run without --dry-run to create the role.", file=sys.stderr)
        return 0

    # ---- real run: needs a runtime ARN and credentials ---------------------
    if not args.runtime:
        print(
            "--runtime is required: pass the AgentCore Runtime ARN the session "
            "will run on (arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<id>), "
            "or set $BMA_RUNTIME_ARN. Use --dry-run to preview the policies first.",
            file=sys.stderr,
        )
        return 1

    runtime_arn = args.runtime
    region = _runtime_region(runtime_arn)
    runtime_account = _runtime_account(runtime_arn)
    if not region or not runtime_account:
        print(
            f"Malformed --runtime ARN {runtime_arn!r}: expected "
            "arn:<partition>:bedrock-agentcore:<region>:<12-digit-account>:runtime/<id> "
            "(no wildcards).",
            file=sys.stderr,
        )
        return 1

    role_name = args.role_name or f"{DEFAULT_ROLE_PREFIX}-{region}"

    # Build the session (optionally pinned to a profile) and resolve the real
    # account id via STS. boto3 replaces the aws CLI here, so no CLI is needed.
    session = boto3.Session(profile_name=args.aws_profile)
    print("Looking up your AWS account id via sts get-caller-identity...", file=sys.stderr)
    try:
        account_id = session.client("sts").get_caller_identity()["Account"]
    except Exception as exc:  # noqa: BLE001 — creds may be absent/expired; report clearly
        print(
            f"Could not resolve your AWS account id ({exc}). "
            "Check your credentials/profile, or use --dry-run.",
            file=sys.stderr,
        )
        return 1
    if not account_id or account_id == "None":
        print(
            "Could not resolve your AWS account id. Check your credentials/profile.",
            file=sys.stderr,
        )
        return 1
    print(f"Account id: {account_id}", file=sys.stderr)

    # Cross-check the caller account against the Runtime ARN's account. The
    # role is created in the CALLER's account, so a mismatch means the policy
    # would grant invoke permissions on a Runtime in a different account —
    # almost always the wrong profile or a mis-copied ARN. Fail closed; a
    # genuine cross-account setup is out of scope for this sample.
    if runtime_account != account_id:
        print(
            f"ERROR: the --runtime ARN's account ({runtime_account}) does not "
            f"match your caller account ({account_id}). The role is created in "
            "YOUR account, so this is almost certainly the wrong AWS profile or "
            "a mis-copied Runtime ARN. Fix one of them and re-run. (Cross-account "
            "Runtime setups are out of scope for this sample.)",
            file=sys.stderr,
        )
        return 1

    role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

    # Show exactly what will be created before doing it.
    print(
        "\n".join(
            [
                "",
                "About to create (if it does not already exist):",
                f"  Role name : {role_name}",
                f"  Role ARN  : {role_arn}",
                f"  Model     : {model}",
                f"  Runtime   : {runtime_arn}",
                "",
                "Trust policy:",
            ]
        ),
        file=sys.stderr,
    )
    print(_dumps(trust_policy(account_id, region)), file=sys.stderr)
    print("", file=sys.stderr)
    print(f"Inline policy ({INLINE_POLICY_NAME}):", file=sys.stderr)
    print(_dumps(session_policy(model, runtime_arn)), file=sys.stderr)

    # Confirm unless --yes.
    if not args.yes:
        print("", file=sys.stderr)
        reply = input("Proceed with creating/updating this role? [y/N] ")
        if reply not in ("y", "Y", "yes", "YES"):
            print("Aborted. Nothing was created.", file=sys.stderr)
            return 0

    iam = session.client("iam")

    # Detect an existing role and inform rather than hard-erroring.
    try:
        iam.get_role(RoleName=role_name)
    except iam.exceptions.NoSuchEntityException:
        print(f"Creating role {role_name}...", file=sys.stderr)
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy(account_id, region)),
            Description="Session role assumed by Bedrock Managed Agents (preview) for inference and AgentCore Runtime.",
        )
        print("Role created.", file=sys.stderr)
    else:
        print(f"Role {role_name} already exists; leaving its trust policy as-is.", file=sys.stderr)
        print(f"Updating/ensuring the inline policy {INLINE_POLICY_NAME}.", file=sys.stderr)

    print(f"Putting inline policy {INLINE_POLICY_NAME} on {role_name}...", file=sys.stderr)
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=INLINE_POLICY_NAME,
        PolicyDocument=json.dumps(session_policy(model, runtime_arn)),
    )
    print("Inline policy applied.", file=sys.stderr)

    # Print the caller-side policies for manual attach.
    print_caller_policies(account_id, role_name)

    print("", file=sys.stderr)
    print("Done. Session role ARN:", file=sys.stderr)
    # stdout gets ONLY the bare ARN, so it can be captured in a pipeline.
    print(role_arn)

    # Tell the user how the labs/preflight will find this role. If this is the
    # default region-scoped name, the labs resolve it automatically from the
    # Runtime ARN's Region and the caller's account id — no action needed. If
    # it is a CUSTOM name, the labs would otherwise build the DEFAULT-named
    # ARN, so print a ready-to-paste export of BMA_INFERENCE_ROLE_ARN (the env
    # var resolve_session_role_arn checks first) so every lab and the preflight
    # target this exact role.
    if role_name == f"{DEFAULT_ROLE_PREFIX}-{region}":
        print(
            "\n"
            "This is the default role name, so the labs and preflight resolve it\n"
            "automatically from the Runtime ARN's Region and your account id — no\n"
            "--role-arn needed.",
            file=sys.stderr,
        )
    else:
        print(
            "\n"
            "You used a custom role name, so point the labs and preflight at it by\n"
            "exporting this in each terminal (or pass --role-arn <arn> per command):\n"
            "\n"
            f"  export BMA_INFERENCE_ROLE_ARN={role_arn}            # macOS / Linux\n"
            f'  $env:BMA_INFERENCE_ROLE_ARN="{role_arn}"            # Windows (PowerShell)',
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
