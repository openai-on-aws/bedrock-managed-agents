"""Lab 03 — Continue a session (the Runtime workspace persists within it).

Lab 01 created a session on an AgentCore Runtime and had the agent write
hello.txt. This lab submits a FOLLOW-UP turn to that SAME session and asks
the agent to append a line to that file and show it. If the appended line is
there, you have just seen that the Runtime **workspace persists within the
session**: the microVM's files survive from one turn to the next, not just
the conversation history.

A follow-up turn is submitted through the **events** endpoint with an
``agent.session.input.message`` event, then you drain the turn's events
exactly like Lab 01.

> Note: the workspace is EPHEMERAL across an idle-stop. If the session's
> Runtime is stopped (idle timeout) and later resumed, the files written in
> an earlier turn are not guaranteed to still be there unless persistent
> session storage is configured when the Runtime is provisioned — a future
> advanced lab covers mounting session storage. Within a live session the
> workspace persists, which is what this lab demonstrates.

Run it (from the module root, with the venv active):

    python labs/lab-03-continue-session/continue_session.py \\
        --session-id sess_abc123 \\
        --runtime arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-runtime-id

Docs:
  https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-sessions.html
"""

import argparse
import os
import sys

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, MODULE_ROOT)

import bma_common  # noqa: E402
# The default model, failure-event set, and the turn-draining loop are shared
# across the Module 2 labs (see bma_common.py). This lab's point is the
# follow-up turn against an existing session, so the default prompt below asks
# the agent to touch the file Lab 01 created — proving the workspace persisted.
DEFAULT_PROMPT = "append a line to hello.txt and show it"


def _message_event(text: str) -> dict:
    """Build an input-message event to submit a follow-up turn."""
    return {
        "type": "agent.session.input.message",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        ],
    }


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Submit a follow-up turn to an existing BMA Runtime session, "
            "showing the microVM workspace persists within the session."
        )
    )
    parser.add_argument(
        "--session-id",
        required=True,
        help="Existing session to continue (e.g. sess_abc123). Required.",
    )
    parser.add_argument(
        "prompt",
        metavar="PROMPT",
        nargs="?",
        default=DEFAULT_PROMPT,
        help=(
            "The follow-up user prompt for this turn (default: "
            f"{DEFAULT_PROMPT!r})."
        ),
    )
    bma_common.add_connection_args(parser)
    bma_common.add_runtime_arg(parser)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    # The follow-up turn must hit the SAME Runtime (hence Region) the session
    # was created on. --runtime is required so the Region is unambiguous.
    if not args.runtime:
        print(
            "--runtime is required: pass the SAME AgentCore Runtime ARN the "
            "session was created on (arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<id>), "
            "or set $BMA_RUNTIME_ARN.",
            file=sys.stderr,
        )
        return 1
    region = bma_common.region_from_runtime_arn(args.runtime)
    if not region:
        print(
            f"Malformed --runtime ARN {args.runtime!r}: expected "
            "arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<id>.",
            file=sys.stderr,
        )
        return 1

    client = bma_common.make_client(region, args.aws_profile)

    session_id = args.session_id
    print(f"Continuing session: {session_id}", file=sys.stderr)
    print(f"--- prompt: {args.prompt!r}", file=sys.stderr)

    # Submit the follow-up turn via the events endpoint, then drain it. The
    # agent reconnects to the SAME microVM workspace, so hello.txt from Lab 01
    # is still there to append to.
    client.beta.agents.sessions.events.create(
        session_id, events=[_message_event(args.prompt)]
    )
    bma_common.drain_turn(client, session_id)

    # Reproduce the connection flags so the follow-up delete command targets
    # the SAME Runtime/profile (a mismatch gives a 404).
    conn = bma_common.connection_flags(args)
    print(
        f"\nSession kept open: {session_id}\n"
        "  * delete it when done: python labs/lab-04-delete-session/"
        f"delete_session.py{conn} {session_id}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
