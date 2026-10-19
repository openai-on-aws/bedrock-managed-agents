"""Gateway Lambda: validate dimensions and invoke exactly one fixed SSM document."""

import base64
import json
import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from cad_workflow.contract import digest, job_id, json_bytes, request


def clients():
    config = Config(connect_timeout=5, read_timeout=15,
                    retries={"total_max_attempts": 1, "mode": "standard"})
    session = boto3.Session(region_name=os.environ.get("AWS_REGION"))
    return session.client("s3", config=config), session.client("ssm", config=config)


def read_json(client, bucket, key):
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except client.exceptions.NoSuchKey:
        return None
    with response["Body"] as body:
        data = body.read(128 * 1024 + 1)
    if len(data) > 128 * 1024:
        raise ValueError("Job record exceeds the response limit")
    return json.loads(data)


def result(client, bucket, identifier):
    value = read_json(client, bucket, f"jobs/{identifier}/result.json")
    if value is None:
        return {"job_id": identifier, "status": "pending", "accepted": False,
                "next_action": "Call get_result for this job_id after a few seconds."}
    return value


def create(client, ssm, bucket, parameters):
    key = f"jobs/{parameters['job_id']}/request.json"
    body = json_bytes(parameters)
    existing = read_json(client, bucket, key)
    if existing is not None and existing != parameters:
        raise ValueError("Job ID is already reserved with different dimensions")
    if existing is None:
        try:
            client.put_object(Bucket=bucket, Key=key, Body=body, IfNoneMatch="*",
                              ServerSideEncryption="AES256", ContentType="application/json")
        except ClientError as error:
            # A concurrent identical reservation is safe; a different one is not.
            if error.response["Error"]["Code"] not in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise
            if read_json(client, bucket, key) != parameters:
                raise ValueError("Conflicting concurrent job reservation") from error
    completed = read_json(client, bucket, f"jobs/{parameters['job_id']}/result.json")
    if completed is not None:
        return completed
    # Duplicate delivery is resolved by the worker's lock and immutable job ID.
    # The agent never supplies a command, file path, bucket or instance.
    command = ssm.send_command(
        InstanceIds=[os.environ["CAD_INSTANCE"]],
        DocumentName=os.environ["CAD_DOCUMENT"],
        Parameters={"RequestBase64": [base64.b64encode(body).decode()]},
        TimeoutSeconds=300,
        CloudWatchOutputConfig={"CloudWatchLogGroupName": os.environ["CAD_LOG_GROUP"],
                                "CloudWatchOutputEnabled": True},
        Comment=f"CAD support job {parameters['job_id']}",
    )
    print(json.dumps({"event": "job_dispatched", "job_id": parameters["job_id"],
                      "request_sha256": digest(body), "command_id": command["Command"]["CommandId"]}))
    current = result(client, bucket, parameters["job_id"])
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        if current["status"] != "pending":
            return current
        time.sleep(2)
        current = result(client, bucket, parameters["job_id"])
    return current


def handler(event, context):
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    tool = custom.get("bedrockAgentCoreToolName", "").rsplit("___", 1)[-1]
    if tool not in ("create_support", "get_result"):
        raise ValueError("Unknown Gateway tool")
    if tool == "create_support":
        parameters = request(event)
        client, ssm = clients()
        return create(client, ssm, os.environ["CAD_BUCKET"], parameters)
    if not isinstance(event, dict) or set(event) != {"job_id"}:
        raise ValueError("get_result accepts only job_id")
    identifier = job_id(event["job_id"])
    client, _ = clients()
    bucket = os.environ["CAD_BUCKET"]
    value = result(client, bucket, identifier)
    if value["status"] == "pending":
        time.sleep(2)
        value = result(client, bucket, identifier)
    return value
