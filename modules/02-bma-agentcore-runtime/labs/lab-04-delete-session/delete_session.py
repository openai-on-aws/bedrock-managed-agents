"""Lab 04 — Delete one or more sessions.

Sessions are never auto-deleted, so when you are done testing, clean them
up explicitly. This lab deletes one or more session ids, reporting success
or failure per id and continuing past any failure so one bad id does not
stop the rest. The process exits non-zero if any delete failed.

Deleting a BMA session removes the session and its conversation history. It
does NOT delete the AgentCore Runtime the session ran on (that is managed
separately with the AgentCore CLI, e.g. `agentcore remove`), nor the
BmaSessionRole-<region> IAM role, nor any customer-owned files or resources
the agent may have touched — those are yours to manage separately.

Run it (from the module root, with the venv active):

    python labs/lab-04-delete-session/delete_session.py sess_abc123
    python labs/lab-04-delete-session/delete_session.py sess_abc123 sess_def456

Docs:
  https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-sessions.html
"""

import argparse
import os
import sys

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, MODULE_ROOT)

import bma_common  # noqa: E402
def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delete one or more BMA sessions.")
    parser.add_argument(
        "session_ids",
        nargs="+",
        metavar="SESSION_ID",
        help="One or more session IDs to delete (e.g. sess_abc123).",
    )
    bma_common.add_connection_args(parser)
    # --runtime is optional here: deleting only needs the Region, which the
    # Runtime ARN also carries, so you can pass --runtime instead of --region
    # to match how the session was created.
    bma_common.add_runtime_arg(parser)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    # Delete in the SAME Region the session was created in. The Region can come
    # from --region or (if given) the --runtime ARN; a mismatch returns a 404
    # even though the session exists elsewhere.
    region = (
        bma_common.region_from_runtime_arn(args.runtime)
        if args.runtime
        else None
    ) or args.region

    client = bma_common.make_client(region, args.aws_profile)

    exit_code = 0
    for session_id in args.session_ids:
        # Delete each id independently. If one fails (already gone, bad id,
        # permissions), report it and keep going with the rest, then flag the
        # overall run as failed with a non-zero exit code.
        try:
            client.beta.agents.sessions.delete(session_id)
            print(f"Deleted session: {session_id}")
        except Exception as exc:  # noqa: BLE001 — report and continue with the rest
            print(f"Failed to delete session {session_id}: {exc}", file=sys.stderr)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
