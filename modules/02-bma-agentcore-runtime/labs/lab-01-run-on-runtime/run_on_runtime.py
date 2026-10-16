"""Lab 01 — Run an agent on an AgentCore Runtime (it executes commands).

This is the headline lab of Module 2. Unlike Module 1's
``environment={"type": "none"}`` (where the agent only reasons and produces
text), here the agent runs on an **AgentCore Runtime** — a managed microVM —
so it can actually EXECUTE commands against a workspace. It does the smallest
useful thing end to end:

  1. build the client (bearer-token auth, no OpenAI API key),
  2. create a session whose environment is the AgentCore Runtime you name
     with --runtime (``environment=bma_common.agentcore_environment(...)``),
  3. submit the first prompt as that session's first turn, and
  4. stream the lifecycle events until the turn finishes, printing the
     agent's text output.

Because the agent now runs in a sandbox, its work shows up as
``command_execution`` items (the ``$`` command it ran plus the command's
output) — inspect them in Lab 02. The default prompt is a neutral shell task:
create a file, list the workspace, and show the file's contents.

It never deletes the session — it prints the new session id so later labs
(interpret results, continue the session, delete) can reuse it.

Run it (from the module root, with the venv active):

    python labs/lab-01-run-on-runtime/run_on_runtime.py \\
        --runtime arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-runtime-id

Docs:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-bma.html
  https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-sessions.html
"""

import argparse
import json
import os
import sys

# --- Import the shared module helpers ------------------------------------
# Each lab lives three directories below the module root:
#   modules/02-bma-agentcore-runtime/labs/lab-01-run-on-runtime/run_on_runtime.py
#   module root ----------------------^
# bma_common.py sits at that module root. Insert it on sys.path so
# `import bma_common` works no matter which directory you run the script
# from. os.path.dirname three times walks up from this file to the module root.
MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, MODULE_ROOT)

import bma_common  # noqa: E402 — must come after the sys.path shim above
# The model, system instructions, failure-event set, the event-draining loop,
# and the AgentCore environment/role helpers are shared across the Module 2
# labs and live in bma_common.py:
#   bma_common.DEFAULT_MODEL                 — default inference model
#   bma_common.DEFAULT_INSTRUCTIONS          — the "concise assistant" instructions
#   bma_common.make_client(region, profile)  — bearer-token OpenAI client
#   bma_common.resolve_session_role_arn(...)  — the BmaSessionRole-<region> ARN
#   bma_common.agentcore_environment(arn)    — the aws_bedrock_agentcore env dict
#   bma_common.drain_turn(client, session_id) — stream a turn to completion
# This lab just uses them so the create-session flow below stays the focus.

# A neutral shell task that proves the agent EXECUTES commands in the Runtime:
# write a file, list the workspace, and read the file back. Each of these
# shows up as a command_execution item (see Lab 02).
DEFAULT_PROMPT = (
    "Create a file named hello.txt containing 'hello from the runtime', then "
    "list the files in the workspace and show the file's contents."
)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a BMA session on an AgentCore Runtime and stream its first "
            "reply (the agent executes commands in a microVM)."
        )
    )
    parser.add_argument(
        "prompt",
        metavar="PROMPT",
        nargs="?",
        default=DEFAULT_PROMPT,
        help=(
            "The first user prompt, submitted as the session's first turn "
            "(default: the neutral create-file/list/show shell task)."
        ),
    )
    parser.add_argument(
        "--model",
        default=bma_common.DEFAULT_MODEL,
        help=f"Model id for the new session (default: {bma_common.DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--instructions",
        default=bma_common.DEFAULT_INSTRUCTIONS,
        help="System instructions for the new session.",
    )
    bma_common.add_connection_args(parser)
    bma_common.add_runtime_arg(parser)
    bma_common.add_role_arn_arg(parser)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    # The AgentCore Runtime is required: it is the execution environment the
    # agent runs on, and its ARN also carries the Region/account the session
    # inherits.
    if not args.runtime:
        print(
            "--runtime is required: pass the AgentCore Runtime ARN the agent "
            "will run on (arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<id>), "
            "or set $BMA_RUNTIME_ARN. See 00-prerequisites.md on provisioning the "
            "Runtime with the AgentCore CLI.",
            file=sys.stderr,
        )
        return 1

    # The session MUST run in the Runtime's own Region, so derive the Region
    # from the ARN rather than from --region (which the Runtime ARN implies).
    region = bma_common.region_from_runtime_arn(args.runtime)
    if not region:
        print(
            f"Malformed --runtime ARN {args.runtime!r}: expected "
            "arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<id>.",
            file=sys.stderr,
        )
        return 1
    if region != args.region:
        print(
            f"Using Region {region} from the --runtime ARN "
            f"(ignoring --region {args.region}); a session runs in its Runtime's "
            "Region.",
            file=sys.stderr,
        )

    # make_client wires the OpenAI SDK to the regional bedrock-mantle endpoint
    # and authenticates with a short-lived bearer token minted from your AWS
    # credentials — no OpenAI API key.
    client = bma_common.make_client(region, args.aws_profile)

    # The IAM role BMA assumes to run inference AND to invoke the Runtime.
    # resolve_session_role_arn uses --role-arn / $BMA_INFERENCE_ROLE_ARN if
    # set, else builds BmaSessionRole-<region> in your own account id (Region
    # from the Runtime ARN, account from STS get-caller-identity).
    role_arn = bma_common.resolve_session_role_arn(
        args.role_arn, args.runtime, args.aws_profile
    )

    # The agent config: which model to use and how it should behave.
    agent = {
        "model": args.model,
        "instructions": args.instructions,
    }
    print(f"Agent config: {json.dumps(agent, indent=2)}", file=sys.stderr)

    # Create the session AND start its first turn in one call.
    #   environment=agentcore_environment(arn) -> run the agent on the named
    #                                    AgentCore Runtime (a microVM) so it can
    #                                    EXECUTE commands, not just reason.
    #   extra_body {"role_arn": ...}   -> the typed SDK does not expose
    #                                    role_arn yet, but the BMA preview API
    #                                    requires it as a top-level body field;
    #                                    extra_body merges it into the request.
    #   input [...]                    -> the first user message, as content
    #                                    parts of type "input_text".
    #   stream=False                   -> create() returns the session object;
    #                                    we stream the turn's events separately
    #                                    below via events.stream().
    sess = client.beta.agents.sessions.create(
        agent=agent,
        environment=bma_common.agentcore_environment(args.runtime),
        extra_body={"role_arn": role_arn},
        input=[
            {
                "role": "user",
                "content": [{"type": "input_text", "text": args.prompt}],
            }
        ],
        stream=False,
    )
    session_id = sess.id

    # The session id is the handle for everything else: continue it, list its
    # items, or delete it. Print it to stdout so it is easy to capture.
    print(f"Created session: {session_id}")
    print(f"--- prompt: {args.prompt!r}", file=sys.stderr)

    # Drain the first turn that create() already started. The agent executes
    # the shell task in the Runtime; the commands it runs become
    # command_execution items you can inspect in Lab 02.
    bma_common.drain_turn(client, session_id)

    # Reproduce the connection flags so the printed follow-up commands target
    # the SAME Runtime/profile — sessions are tied to one Runtime (and its
    # Region), and a mismatch yields a confusing 404 on continue/inspect/delete.
    conn = bma_common.connection_flags(args)
    print(
        f"\nSession kept open: {session_id}\n"
        "  * inspect its items:   python labs/lab-02-interpret-results/"
        f"interpret_results.py{conn} --session-id {session_id}\n"
        "  * continue the session: python labs/lab-03-continue-session/"
        f"continue_session.py{conn} --session-id {session_id}\n"
        "  * delete it when done:  python labs/lab-04-delete-session/"
        f"delete_session.py{conn} {session_id}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
