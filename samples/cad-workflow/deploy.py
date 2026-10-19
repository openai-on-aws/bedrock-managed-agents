#!/usr/bin/env python3
"""Create a dedicated pilot stack; never update an unrelated or existing stack."""

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from cad_workflow.infrastructure import ROOT, render

AMI_PARAMETER = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def validate(state):
    """Exercise both network variants locally, including negative security controls."""
    subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
                   cwd=ROOT, check=True)
    lint = Path(sys.executable).with_name("cfn-lint")
    if not lint.is_file():
        installed = shutil.which("cfn-lint")
        if not installed:
            raise RuntimeError("Run 'uv sync --frozen' to install this sample's pinned cfn-lint")
        lint = Path(installed)
    guard = ROOT / ".tools/cfn-guard"
    if not guard.is_file():
        installed = shutil.which("cfn-guard")
        if not installed:
            raise RuntimeError("Run 'uv run python install_guard.py' first; see README.md")
        guard = Path(installed)
    templates = {}
    for existing in (False, True):
        name = "template-existing-network.json" if existing else "template.json"
        path = state / name
        templates[name] = render(use_existing_network=existing)
        save(path, templates[name])
        subprocess.run([str(lint), "--format", "json",
                        str(path), "--regions", "us-east-1"], cwd=ROOT, check=True)
        subprocess.run([str(guard), "validate", "--data", str(path),
                        "--rules", str(ROOT / "security.guard"), "--output-format", "json"],
                       cwd=ROOT, check=True)

    mutations = {
        "public-ingress": ("WorkerSecurityGroup", "SecurityGroupIngress",
                           [{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "CidrIp": "0.0.0.0/0"}]),
        "unversioned-artifacts": ("Artifacts", "VersioningConfiguration", {"Status": "Suspended"}),
        "optional-imdsv2": ("Worker", "MetadataOptions",
                           {"HttpTokens": "optional", "HttpPutResponseHopLimit": 1}),
        "unauthenticated-gateway": ("Gateway", "AuthorizerType", "NONE"),
    }
    for name, (resource, property_name, value) in mutations.items():
        candidate = deepcopy(templates["template.json"])
        candidate["Resources"][resource]["Properties"][property_name] = value
        path = state / "negative-controls" / f"{name}.json"
        save(path, candidate)
        checked = subprocess.run(
            [str(guard), "validate", "--data", str(path),
             "--rules", str(ROOT / "security.guard"), "--output-format", "json"],
            cwd=ROOT, capture_output=True, text=True,
        )
        # Guard uses 19 for a rule violation. A parser/tool failure is not a pass.
        if checked.returncode != 19:
            raise RuntimeError(f"Guard did not reject {name} as a rule violation: "
                               f"{checked.stdout}\n{checked.stderr}")
    print(json.dumps({"event": "local_validation_passed", "network_variants": 2,
                      "rejected_security_mutations": len(mutations)}), flush=True)


def existing_stack(client, name):
    try:
        return client.describe_stacks(StackName=name)["Stacks"][0]
    except ClientError as error:
        if error.response["Error"]["Code"] == "ValidationError" and "does not exist" in str(error):
            return None
        raise


def wait_change_set(client, stack, identifier, state):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        change = client.describe_change_set(StackName=stack, ChangeSetName=identifier)
        if change["Status"] not in ("CREATE_PENDING", "CREATE_IN_PROGRESS"):
            save(state / "change-set.json", change)
            if change["Status"] != "CREATE_COMPLETE":
                raise RuntimeError(f"Change set failed: {change.get('StatusReason', change['Status'])}")
            events = client.describe_events(StackName=stack, ChangeSetName=identifier)
            save(state / "validation-events.json", events)
            failed = [event for event in events.get("OperationEvents", [])
                      if event.get("ValidationStatus") == "FAILED"
                      or event.get("EventType") == "VALIDATION_ERROR"]
            if failed:
                raise RuntimeError(f"CloudFormation validation rejected the change set: {failed}")
            print(json.dumps({"event": "change_set_validated",
                              "changes": len(change.get("Changes", [])),
                              "change_set": identifier}), flush=True)
            return
        time.sleep(3)
    raise TimeoutError("Change-set creation exceeded five minutes")


def wait_stack(client, name, state):
    deadline = time.monotonic() + 30 * 60
    seen = set()
    while time.monotonic() < deadline:
        stack = client.describe_stacks(StackName=name)["Stacks"][0]
        events = client.describe_stack_events(StackName=name)["StackEvents"]
        for event in reversed(events):
            if event["EventId"] not in seen:
                seen.add(event["EventId"])
                print(json.dumps({"resource": event["LogicalResourceId"],
                                  "status": event["ResourceStatus"],
                                  "reason": event.get("ResourceStatusReason")}), flush=True)
        status = stack["StackStatus"]
        if status == "CREATE_COMPLETE":
            outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
            save(state / "outputs.json", outputs)
            save(state / "stack.json", stack)
            print(json.dumps({"event": "deployed", "outputs": outputs}), flush=True)
            return
        if not status.endswith("_IN_PROGRESS"):
            save(state / "failed-stack.json", stack)
            save(state / "failed-events.json", events)
            raise RuntimeError(f"Stack ended in {status}; inspect {state}/failed-events.json")
        time.sleep(10)
    raise TimeoutError("Stack creation exceeded 30 minutes; AWS may still be working. Use status.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "deploy", "status"))
    parser.add_argument("--profile", help="AWS profile; otherwise use the standard AWS credential chain")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--account", help="Expected 12-digit AWS account; required for deploy/status")
    parser.add_argument("--stack", default="bma-cad-freecad")
    parser.add_argument("--model", default="openai.gpt-6.1-sol")
    parser.add_argument("--instance-type", choices=("t3.medium", "t3.large"), default="t3.medium")
    parser.add_argument("--hours", type=int, choices=range(1, 25), default=24)
    parser.add_argument("--vpc-id", help="Reuse this VPC without changing its routes")
    parser.add_argument("--subnet-id", help="Existing public subnet in --vpc-id; no inbound ports will be opened")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,31}", args.stack):
        parser.error("--stack must be a letter followed by at most 31 letters, digits or hyphens")
    if args.action != "validate" and (not args.account or not re.fullmatch(r"\d{12}", args.account)):
        parser.error("--account must be a 12-digit account ID")
    if args.region != "us-east-1":
        parser.error("This sample targets us-east-1; qualify BMA and Gateway support before changing regions")
    if bool(args.vpc_id) != bool(args.subnet_id):
        parser.error("--vpc-id and --subnet-id must be provided together")
    if args.action == "validate":
        validate(ROOT / ".state" / "validation")
        return
    if not re.fullmatch(r"openai\.[a-zA-Z0-9.:-]+", args.model):
        parser.error("--model must be an OpenAI model identifier supported by BMA")
    state = ROOT / ".state" / args.stack
    template = render(use_existing_network=bool(args.vpc_id))
    if args.action == "deploy":
        validate(ROOT / ".state" / "validation")
        save(state / "template.json", template)
    config = Config(connect_timeout=5, read_timeout=30,
                    retries={"total_max_attempts": 3, "mode": "standard"})
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    sts = session.client("sts", config=config)
    account = sts.get_caller_identity()["Account"]
    if account != args.account:
        raise RuntimeError(f"Account mismatch: expected {args.account}, authenticated to {account}")
    cfn = session.client("cloudformation", config=config)
    existing = existing_stack(cfn, args.stack)
    if args.action == "status":
        if existing is None:
            raise RuntimeError("Stack does not exist")
        print(json.dumps({"status": existing["StackStatus"],
                          "outputs": existing.get("Outputs", [])}, indent=2))
        return
    if existing is not None:
        only_review = existing["StackStatus"] == "REVIEW_IN_PROGRESS"
        saved_path = state / "change-set-id.json"
        saved = json.loads(saved_path.read_text()) if saved_path.exists() else {}
        owned = saved.get("StackId") == existing["StackId"]
        if only_review and owned:
            previous = cfn.describe_change_set(StackName=args.stack, ChangeSetName=saved["Id"])
            tags = {item["Key"]: item["Value"] for item in previous.get("Tags", [])}
            owned = tags.get("ManagedBy") == "bma-cad-sample" and tags.get("Project") == args.stack
            save(state / "previous-change-set.json", previous)
        if not (only_review and owned and not cfn.list_stack_resources(StackName=args.stack)["StackResourceSummaries"]):
            raise RuntimeError(f"Stack already exists ({existing['StackStatus']}). "
                               "Use status, or choose a new stack name. Deploy never silently updates workers.")
    if args.vpc_id:
        ec2 = session.client("ec2", config=config)
        subnet = ec2.describe_subnets(SubnetIds=[args.subnet_id])["Subnets"][0]
        if subnet["VpcId"] != args.vpc_id or subnet["AvailableIpAddressCount"] < 1:
            raise RuntimeError("Existing subnet must belong to the selected VPC and have an available IP")
        tables = [table for page in ec2.get_paginator("describe_route_tables").paginate(
            Filters=[{"Name": "vpc-id", "Values": [args.vpc_id]}]) for table in page["RouteTables"]]
        associated = [table for table in tables if any(
            a.get("SubnetId") == args.subnet_id for a in table["Associations"])]
        selected = associated or [table for table in tables if any(
            a.get("Main") for a in table["Associations"])]
        if len(selected) != 1 or not any(
            route.get("DestinationCidrBlock") == "0.0.0.0/0"
            and route.get("GatewayId", "").startswith("igw-") and route.get("State") == "active"
            for route in selected[0]["Routes"]
        ):
            raise RuntimeError("Existing subnet requires an active Internet Gateway route for this pilot")
    ssm = session.client("ssm", config=config)
    ami = ssm.get_parameter(Name=AMI_PARAMETER)["Parameter"]
    stop_at = (datetime.now(timezone.utc) + timedelta(hours=args.hours)).replace(microsecond=0)
    parameters = {
        "AmiId": ami["Value"], "InstanceType": args.instance_type,
        "ModelId": args.model, "StopAtUtc": stop_at.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if args.vpc_id:
        parameters.update(ExistingVpcId=args.vpc_id, ExistingSubnetId=args.subnet_id)
    plan = {"account": account, "region": args.region, "stack": args.stack,
            "parameters": parameters, "ami_parameter": AMI_PARAMETER,
            "ami_parameter_version": ami["Version"],
            "worker_source_sha256": template["Metadata"]["SourceSha256"]}
    save(state / "deployment.json", plan)
    print(json.dumps({"event": "deployment_plan", **plan}), flush=True)
    body = json.dumps(template, separators=(",", ":"))
    if len(body.encode()) > 51200:
        raise RuntimeError("Template exceeds the inline CloudFormation limit")
    change = cfn.create_change_set(
        StackName=args.stack, ChangeSetName="deploy-" + uuid.uuid4().hex[:12],
        ChangeSetType="CREATE", TemplateBody=body,
        Parameters=[{"ParameterKey": key, "ParameterValue": value} for key, value in parameters.items()],
        Capabilities=["CAPABILITY_IAM"],
        Tags=[{"Key": "ManagedBy", "Value": "bma-cad-sample"},
              {"Key": "Project", "Value": args.stack}],
        Description="Dedicated FreeCAD/BMA pilot with private artifacts and a timed worker stop",
    )
    save(state / "change-set-id.json", change)
    wait_change_set(cfn, args.stack, change["Id"], state)
    cfn.execute_change_set(StackName=args.stack, ChangeSetName=change["Id"])
    wait_stack(cfn, args.stack, state)


if __name__ == "__main__":
    main()
