#!/usr/bin/env python3
"""Stop sample compute, delete its stack, or delete a recorded BMA session."""

import argparse
from datetime import datetime, timezone
import json
import re

import boto3
from botocore.config import Config
from openai import NotFoundError, OpenAI
from openai.providers import bedrock

from cad_workflow.contract import job_id
from run import ROOT, save


def session_record(identifier, account, region, stack):
    directory = ROOT / "artifacts" / job_id(identifier)
    context = json.loads((directory / "aws-context.json").read_text())
    expected = {"account": account, "region": region, "stack": stack}
    if any(context.get(key) != value for key, value in expected.items()):
        raise ValueError("Saved job belongs to a different account, region or stack")
    session_id = json.loads((directory / "bma-session.json").read_text()).get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Saved job has no BMA session ID")
    return directory, session_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stop", "delete-session", "delete-stack"))
    parser.add_argument("--profile", help="AWS profile; otherwise use the standard credential chain")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--account", required=True)
    parser.add_argument("--stack", default="bma-cad-freecad")
    parser.add_argument("--job-id", help="Recorded local job; required for delete-session")
    args = parser.parse_args()
    if not re.fullmatch(r"\d{12}", args.account):
        parser.error("--account must be a 12-digit account ID")
    if args.action == "delete-session" and not args.job_id:
        parser.error("delete-session requires --job-id")
    if args.action != "delete-session" and args.job_id:
        parser.error("--job-id applies only to delete-session")
    recorded = None
    if args.action == "delete-session":
        # Validate the local record before making any AWS call.
        recorded = session_record(args.job_id, args.account, args.region, args.stack)

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    config = Config(connect_timeout=5, read_timeout=30,
                    retries={"total_max_attempts": 2, "mode": "standard"})
    if session.client("sts", config=config).get_caller_identity()["Account"] != args.account:
        raise ValueError("Authenticated account does not match --account")
    if recorded:
        directory, identifier = recorded
        with OpenAI(provider=bedrock(region=args.region, endpoint="mantle", profile=args.profile),
                    timeout=30, max_retries=0) as client:
            try:
                client.beta.agents.sessions.delete(identifier)
            except NotFoundError:
                pass
        record = {"session_id": identifier, "deleted_at": datetime.now(timezone.utc).isoformat()}
        save(directory / "bma-deletion.json", record)
        print(json.dumps({"event": "session_deleted", **record}))
        return

    cfn = session.client("cloudformation", config=config)
    stack = cfn.describe_stacks(StackName=args.stack)["Stacks"][0]
    tags = {item["Key"]: item["Value"] for item in stack.get("Tags", [])}
    if tags.get("ManagedBy") != "bma-cad-sample" or tags.get("Project") != args.stack:
        raise ValueError("Refusing to change a stack not owned by this CAD sample")
    if stack["StackStatus"].endswith("_IN_PROGRESS"):
        raise ValueError("Wait for the current stack operation before requesting cleanup")
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}
    if args.action == "stop":
        if "WorkerId" not in outputs:
            raise ValueError("Stack has no worker output; inspect it with deploy.py status")
        session.client("ec2", config=config).stop_instances(InstanceIds=[outputs["WorkerId"]])
        print(json.dumps({"event": "worker_stop_requested", "worker_id": outputs["WorkerId"],
                          "note": "EBS, S3 and log storage remain billable."}))
        return
    save(ROOT / ".state" / args.stack / "before-delete.json", stack)
    cfn.delete_stack(StackName=stack["StackId"])
    print(json.dumps({"event": "stack_delete_requested", "stack_id": stack["StackId"],
                      "retained_artifact_bucket": outputs.get("ArtifactBucket"),
                      "note": "Stack deletion terminates EC2 and deletes its root disk and log groups. "
                              "The S3 bucket and BMA sessions are retained."}))


if __name__ == "__main__":
    main()
