#!/usr/bin/env python3
"""Ask BMA to build a support part, then download and hash-check its native evidence."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
import time
import uuid

import boto3
from botocore.config import Config
from openai import OpenAI
from openai.providers import bedrock

from cad_workflow.backends import get_backend
from cad_workflow.contract import (
    MAX_FILE_BYTES, SCHEMA, digest, json_bytes, request, require_verified_report,
)

ROOT = Path(__file__).resolve().parent
TERMINAL = {"completed", "failed", "cancelled"}
INSTRUCTIONS = """You operate a bounded native CAD workflow. The user supplies a job ID and
dimensions. Call create_support once with exactly those values. If it returns pending,
call get_result until it returns accepted or failed, at most 15 times. Never invent an
artifact, measurement, or successful check. If verification fails, report the error.
If it succeeds, state the native files, blind pocket depth, floor thickness, and checks.
Do not substitute free text for a tool call. Do not submit a different job ID."""


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")


def output(data):
    print(json.dumps(data, default=str), flush=True)


def stack_outputs(client, name):
    stack = client.describe_stacks(StackName=name)["Stacks"][0]
    tags = {item["Key"]: item["Value"] for item in stack.get("Tags", [])}
    if tags.get("ManagedBy") != "bma-cad-sample":
        raise ValueError("Stack is not managed by this CAD sample")
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise ValueError(f"Stack is not ready: {stack['StackStatus']}")
    return {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}


def read_object(client, bucket, key, version=None):
    arguments = {"Bucket": bucket, "Key": key}
    if version:
        arguments["VersionId"] = version
    response = client.get_object(**arguments)
    if response["ContentLength"] > MAX_FILE_BYTES:
        response["Body"].close()
        raise ValueError("Artifact is too large")
    with response["Body"] as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Artifact exceeds download limit")
    return data


def download(client, outputs, parameters, destination):
    backend = get_backend(outputs["CadBackend"])
    identifier = parameters["job_id"]
    bucket = outputs["ArtifactBucket"]
    deadline = time.monotonic() + 240
    while True:
        try:
            result = json.loads(read_object(client, bucket, f"jobs/{identifier}/result.json"))
            break
        except client.exceptions.NoSuchKey:
            if time.monotonic() >= deadline:
                raise TimeoutError("No native job receipt arrived within four minutes")
            time.sleep(3)
    if result.get("parameters") != parameters or result.get("job_id") != identifier:
        raise ValueError("Native receipt does not match the submitted task")
    if result.get("schema") != SCHEMA or result.get("backend") != backend.NAME:
        raise ValueError("Native receipt uses an unexpected schema or backend")
    if result.get("request_sha256") != digest(json_bytes(parameters)):
        raise ValueError("Native receipt has an incorrect request hash")
    if ((result.get("status"), result.get("accepted")) not in
            (("accepted", True), ("failed", False)) or type(result.get("accepted")) is not bool):
        raise ValueError("Native receipt contains an inconsistent verdict")
    files = result.get("artifacts", {})
    allowed = {*backend.CANDIDATE_FILES, "checks.json", "failure.txt",
               "author.log", "verifier.log", "author-error.txt", "verifier-error.txt"}
    if not isinstance(files, dict) or not set(files) <= allowed:
        raise ValueError("Native receipt contains unexpected artifacts")
    for name, record in files.items():
        if record["key"] != f"jobs/{identifier}/{name}" or not record.get("version_id"):
            raise ValueError("Artifact key or version does not match the receipt")
        data = read_object(client, bucket, record["key"], record["version_id"])
        if digest(data) != record["sha256"] or len(data) != record["bytes"]:
            raise ValueError(f"Artifact hash or size mismatch: {name}")
        (destination / name).write_bytes(data)
    save(destination / "native-result.json", result)
    if result["accepted"]:
        if not {*backend.CANDIDATE_FILES, "checks.json"} <= set(files):
            raise ValueError("Accepted receipt is missing native artifacts or checks")
        checks = json.loads((destination / "checks.json").read_text())
        if checks != result.get("checks"):
            raise ValueError("Downloaded check report differs from the accepted receipt")
        require_verified_report(
            checks, backend.NAME,
            {name: digest((destination / name).read_bytes()) for name in backend.CHECKED_FILES},
        )
    return result


def run_bma(profile, region, outputs, parameters, destination):
    started = time.monotonic()
    session_id = None
    completed = False
    timer = None
    with OpenAI(provider=bedrock(region=region, endpoint="mantle", profile=profile),
                timeout=30, max_retries=0) as client:
        try:
            session = client.beta.agents.sessions.create(
                environment={"type": "none"},
                agent={
                    "model": outputs["ModelId"], "service_tier": "default",
                    "reasoning": {"effort": "low"},
                    "instructions": INSTRUCTIONS,
                    "tools": [{"type": "mcp", "server_label": "cad",
                               "connector_id": outputs["GatewayArn"]}],
                },
                extra_body={"role_arn": outputs["SessionRoleArn"]},
                input=[{"role": "user", "content": [{
                    "type": "input_text",
                    "text": "Create and verify this support part: " + json.dumps(parameters),
                }]}],
                stream=False,
            )
            session_id = session.id
            save(destination / "bma-session.json", session.model_dump(mode="json", warnings=False))
            output({"event": "bma_session_created", "session_id": session_id,
                    "job_id": parameters["job_id"], "model": outputs["ModelId"]})
            turns = []
            def cancel_at_deadline():
                try:
                    with OpenAI(provider=bedrock(region=region, endpoint="mantle", profile=profile),
                                timeout=20, max_retries=0) as cancellation_client:
                        cancellation_client.beta.agents.sessions.events.create(
                            session_id, events=[{"type": "agent.session.input.cancel"}],
                            idempotency_key=f"deadline-{parameters['job_id']}",
                        )
                except Exception as error:
                    save(destination / "cancellation-error.json",
                         {"type": type(error).__name__, "message": str(error)})

            timer = threading.Timer(300, cancel_at_deadline)
            timer.daemon = True
            timer.start()
            # BMA supports the session event stream; the SDK's separate turns
            # REST endpoint is not implemented by this provider.
            with (destination / "bma-events.jsonl").open("w") as journal, \
                 client.beta.agents.sessions.events.stream(
                     session_id, extra_query={"stream": "true"}, timeout=60) as events:
                for event in events:
                    data = event.model_dump(mode="json", warnings=False)
                    journal.write(json.dumps(data, allow_nan=False) + "\n")
                    journal.flush()
                    kind = data["type"]
                    if kind == "agent.session.turn.item.done" and data.get("item", {}).get("type") == "mcp_call":
                        item = data["item"]
                        output({"event": "bma_tool_completed", "name": item.get("name"),
                                "status": item.get("status"), "error": item.get("error")})
                    if kind.startswith("agent.session.turn.") and kind.rsplit(".", 1)[-1] in TERMINAL:
                        status = kind.rsplit(".", 1)[-1]
                        turns.append({**data.get("turn", {}), "status": status, "event": data})
                        output({"event": "bma_turn_status", "status": status})
                        completed = True
                        break
                    if kind in ("agent.session.failed", "agent.session.environment.failed", "error"):
                        raise RuntimeError(f"BMA lifecycle failure: {json.dumps(data)}")
            if not completed:
                raise TimeoutError("BMA turn exceeded five minutes")
            save(destination / "bma-turns.json", turns)
            items = [item.model_dump(mode="json", warnings=False)
                     for item in client.beta.agents.sessions.items.list(session_id, order="asc", limit=100)]
            save(destination / "bma-items.json", items)
            tools = [item for item in items if item.get("type") == "mcp_call"]
            for item in tools:
                output({"event": "bma_tool", "name": item.get("name"),
                        "status": item.get("status"), "error": item.get("error")})
            for item in items:
                if item.get("type") == "message" and item.get("role") == "assistant":
                    for content in item.get("content", []):
                        if content.get("type") == "output_text":
                            print(content["text"], flush=True)
            receipt = {"session_id": session_id, "model": outputs["ModelId"],
                       "service_tier": "default", "reasoning_effort": "low",
                       "elapsed_seconds": round(time.monotonic() - started, 3),
                       "tool_calls": len(tools), "turns": turns,
                       "completed_at": datetime.now(timezone.utc).isoformat()}
            save(destination / "bma-receipt.json", receipt)
            if not turns or turns[0].get("status") != "completed":
                raise RuntimeError("BMA turn did not complete; inspect bma-turns.json")
            if not any(item.get("name", "").endswith("create_support") for item in tools):
                raise RuntimeError("BMA did not call the native CAD creation tool; inspect bma-items.json")
            return receipt
        finally:
            if timer is not None:
                timer.cancel()
            if session_id and not completed:
                # Stop this client's own turn on timeout, Ctrl-C or transport failure.
                try:
                    client.beta.agents.sessions.events.create(
                        session_id, events=[{"type": "agent.session.input.cancel"}],
                        idempotency_key=f"cancel-{parameters['job_id']}",
                    )
                except Exception as error:
                    # Preserve the original failure and the ID needed for manual cleanup.
                    save(destination / "cancellation-error.json",
                         {"session_id": session_id, "type": type(error).__name__, "message": str(error)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="AWS profile; otherwise use the standard AWS credential chain")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--account", required=True)
    parser.add_argument("--stack", default="bma-cad-freecad")
    parser.add_argument("--thickness", type=float, default=14)
    parser.add_argument("--depth", type=float, default=12)
    parser.add_argument("--job-id", default=None)
    args = parser.parse_args()
    if not re.fullmatch(r"\d{12}", args.account):
        parser.error("--account must be a 12-digit account ID")
    parameters = request({"job_id": args.job_id or uuid.uuid4().hex,
                          "stock_thickness_mm": args.thickness, "pocket_depth_mm": args.depth})
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    config = Config(connect_timeout=5, read_timeout=20,
                    retries={"total_max_attempts": 2, "mode": "standard"})
    sts = session.client("sts", config=config)
    if sts.get_caller_identity()["Account"] != args.account:
        raise ValueError("Authenticated account does not match --account")
    cfn = session.client("cloudformation", config=config)
    outputs = stack_outputs(cfn, args.stack)
    get_backend(outputs["CadBackend"])
    ec2 = session.client("ec2", config=config)
    state = ec2.describe_instances(InstanceIds=[outputs["WorkerId"]])["Reservations"][0]["Instances"][0]["State"]["Name"]
    if state != "running":
        raise RuntimeError(f"Worker is {state}; see README.md before extending the pilot")
    destination = ROOT / "artifacts" / parameters["job_id"]
    destination.mkdir(parents=True, exist_ok=False)
    save(destination / "request.json", parameters)
    save(destination / "aws-context.json", {"account": args.account, "region": args.region,
                                           "stack": args.stack, "backend": outputs["CadBackend"]})
    s3 = session.client("s3", config=config)
    try:
        bma = run_bma(args.profile, args.region, outputs, parameters, destination)
    except Exception:
        # The fixed SSM job may finish even if BMA or its event stream fails.
        # Preserve native evidence where available, while retaining the BMA failure.
        try:
            download(s3, outputs, parameters, destination)
        except Exception as error:
            save(destination / "download-error.json",
                 {"type": type(error).__name__, "message": str(error)})
        raise
    result = download(s3, outputs, parameters, destination)
    output({"event": "native_result", "accepted": result["accepted"],
            "job_id": parameters["job_id"], "native_seconds": result["elapsed_seconds"],
            "bma_seconds": bma["elapsed_seconds"], "artifact_directory": str(destination)})
    if not result.get("accepted"):
        raise RuntimeError("Native verification failed; inspect downloaded diagnostics")


if __name__ == "__main__":
    main()
