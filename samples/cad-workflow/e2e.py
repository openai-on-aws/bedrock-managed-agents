#!/usr/bin/env python3
"""Qualify one example on AWS, save a JSON report, delete its session and stop its worker."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import boto3
from botocore.config import Config
from jsonschema import ValidationError

from cad_workflow.backends import get_backend
from cad_workflow.contract import digest
from cad_workflow.example import load_example, validate_document, verify_saved_run
from cad_workflow.infrastructure import render
from run import ROOT, read_object, save, stack_outputs


def now():
    return datetime.now(timezone.utc).isoformat()


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, allow_nan=False), flush=True)


class Run:
    def __init__(self, args, example):
        self.args = args
        self.example = example
        self.job_id = uuid.uuid4().hex
        self.directory = ROOT / ".state" / "e2e" / self.job_id
        self.artifacts = ROOT / "artifacts" / self.job_id
        self.report_path = self.directory / "report.json"
        self.started_ms = int(time.time() * 1000)
        self.connection = ["--region", args.region, "--account", args.account, "--stack", args.stack]
        if args.profile:
            self.connection += ["--profile", args.profile]
        source_files = (
            "e2e.py", "run.py", "audit.py", "cleanup.py", "deploy.py",
            "ops/freecad_audit.py",
            "cad_workflow/example.py", "schemas/example.schema.json",
            "schemas/e2e-report.schema.json", "pyproject.toml", "uv.lock",
        )
        self.report = {
            "schema_version": "bma-cad/e2e-report/v1", "status": "running",
            "example_id": example["id"], "job_id": self.job_id,
            "started_at": now(), "completed_at": None,
            "scope": {"account": args.account, "region": args.region, "stack": args.stack,
                      "deployment_mode": "deploy" if args.deploy else "existing"},
            "source": {
                "worker_sha256": render()["Metadata"]["SourceSha256"],
                "example_sha256": digest(args.example.read_bytes()),
                "client_sha256": {name: digest((ROOT / name).read_bytes()) for name in source_files},
            },
            "parameters": example["parameters"], "stages": [],
            "native": None, "bma": None, "audit": None, "logs": None,
            "cleanup": {"session_deleted": False, "worker_state": None, "artifacts_retained": False},
            "errors": [],
        }
        self.persist()

    def persist(self):
        validate_document(self.report, "e2e-report.schema.json")
        save(self.report_path, self.report)

    @contextmanager
    def stage(self, name):
        started = time.monotonic()
        record = {"name": name, "status": "running", "started_at": now(), "elapsed_seconds": 0}
        self.report["stages"].append(record)
        self.persist()
        emit("stage_started", stage=name, job_id=self.job_id)
        try:
            yield record
        except BaseException as error:
            record.update(status="failed", error=f"{type(error).__name__}: {error}")
            raise
        else:
            record["status"] = "passed"
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self.persist()
            emit("stage_finished", stage=name, status=record["status"],
                 elapsed_seconds=record["elapsed_seconds"])

    def command(self, name, script, arguments, timeout):
        with self.stage(name) as record:
            path = self.directory / f"{name}.log"
            record["log"] = str(path.relative_to(ROOT))
            with path.open("w") as log:
                result = subprocess.run(
                    [sys.executable, "-B", str(ROOT / script), *arguments],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False,
                )
            record["exit_code"] = result.returncode
            if result.returncode:
                raise RuntimeError(f"{name} failed with exit code {result.returncode}; inspect {path}")

    def capture_logs(self, client):
        counts = {}
        deadline = time.monotonic() + 90
        pending = {"worker", "broker"}
        while pending:
            for suffix in tuple(sorted(pending)):
                events = [
                    event
                    for page in client.get_paginator("filter_log_events").paginate(
                        logGroupName=f"/cad/{self.args.stack}/{suffix}",
                        filterPattern=f'"{self.job_id}"', startTime=self.started_ms - 60_000,
                    )
                    for event in page.get("events", [])
                ]
                if events:
                    save(self.artifacts / f"cloudwatch-{suffix}.json", {"events": events})
                    counts[suffix] = len(events)
                    pending.remove(suffix)
            if not pending:
                return counts
            if time.monotonic() >= deadline:
                raise TimeoutError(f"No job-correlated CloudWatch events arrived for {sorted(pending)}")
            time.sleep(5)

    def execute(self):
        outputs = None
        cleanup_allowed = False
        ec2 = s3 = None
        try:
            with self.stage("identity"):
                session = boto3.Session(profile_name=self.args.profile, region_name=self.args.region)
                config = Config(connect_timeout=5, read_timeout=30,
                                retries={"total_max_attempts": 2, "mode": "standard"})
                sts = session.client("sts", config=config)
                cfn = session.client("cloudformation", config=config)
                ec2 = session.client("ec2", config=config)
                s3 = session.client("s3", config=config)
                logs = session.client("logs", config=config)
                if sts.get_caller_identity()["Account"] != self.args.account:
                    raise ValueError("Authenticated account does not match --account")
            if self.args.deploy:
                arguments = ["deploy", *self.connection, "--hours", str(self.args.hours),
                             "--model", self.args.model]
                if self.args.vpc_id:
                    arguments += ["--vpc-id", self.args.vpc_id, "--subnet-id", self.args.subnet_id]
                self.command("deploy", "deploy.py", arguments, timeout=2400)
            with self.stage("worker_provenance"):
                outputs = stack_outputs(cfn, self.args.stack)
                if outputs["CadBackend"] != self.example["backend"]:
                    raise ValueError("Deployed backend differs from the example")
                if outputs["SourceSha256"] != self.report["source"]["worker_sha256"]:
                    raise ValueError("Deployed worker source differs from this checkout")
                bootstrap = json.loads(read_object(s3, outputs["ArtifactBucket"], "health/bootstrap.json"))
                if bootstrap["source_sha256"] != outputs["SourceSha256"]:
                    raise ValueError("Worker bootstrap does not match the deployed source")
                save(self.directory / "bootstrap.json", bootstrap)
                save(self.directory / "outputs.json", outputs)
                instance = ec2.describe_instances(InstanceIds=[outputs["WorkerId"]])["Reservations"][0]["Instances"][0]
                if instance["State"]["Name"] != "running":
                    raise ValueError("Worker must be running; re-arm its deadline before restarting")
                save(self.directory / "instance.json", instance)
                cleanup_allowed = True
            self.command(
                "bma_and_native", "run.py",
                [*self.connection, "--job-id", self.job_id,
                 "--thickness", str(self.example["parameters"]["stock_thickness_mm"]),
                 "--depth", str(self.example["parameters"]["pocket_depth_mm"])],
                timeout=600,
            )
            self.command("native_audit", "audit.py",
                         [*self.connection, "--source-job", self.job_id], timeout=1260)
            with self.stage("verify_evidence"):
                audits = list(self.artifacts.glob("audit-*/audit.json"))
                if len(audits) != 1:
                    raise ValueError("Expected exactly one completed native audit for the new job")
                self.report.update(verify_saved_run(self.example, self.artifacts, audits[0]))
                native = json.loads((self.artifacts / "native-result.json").read_text())
                backend = get_backend(self.example["backend"])
                expected = {name: digest((ROOT / "cad_workflow" / name).read_bytes())
                            for name in ("contract.py", "backends/__init__.py", "worker.py",
                                         *backend.SOURCE_FILES)}
                if native["code_sha256"] != expected:
                    raise ValueError("Executed worker source differs from this checkout")
            with self.stage("cloudwatch_export"):
                self.report["logs"] = self.capture_logs(logs)
        except (Exception, KeyboardInterrupt) as error:
            self.report["errors"].append(f"{type(error).__name__}: {error}")
        finally:
            if cleanup_allowed:
                self.cleanup(ec2, s3, outputs)
            self.report["completed_at"] = now()
            self.report["status"] = "failed" if self.report["errors"] else "passed"
            try:
                self.persist()
            except ValidationError as error:
                self.report["status"] = "failed"
                self.report["errors"].append(f"Report contract failed: {error.message}")
                self.persist()
            emit("e2e_finished", status=self.report["status"], job_id=self.job_id,
                 report=str(self.report_path), artifact_directory=str(self.artifacts))
        return 0 if self.report["status"] == "passed" else 1

    def cleanup(self, ec2, s3, outputs):
        # Stop only the explicitly selected, provenance-checked sample worker.
        # Never delete or replace a stack, bucket, volume or another job's session.
        if (self.artifacts / "bma-session.json").is_file():
            try:
                self.command("delete_session", "cleanup.py",
                             ["delete-session", *self.connection, "--job-id", self.job_id], timeout=90)
                self.report["cleanup"]["session_deleted"] = True
            except Exception as error:
                self.report["errors"].append(f"Session cleanup: {error}")
        try:
            self.command("stop_worker", "cleanup.py", ["stop", *self.connection], timeout=90)
            with self.stage("confirm_stopped"):
                ec2.get_waiter("instance_stopped").wait(
                    InstanceIds=[outputs["WorkerId"]], WaiterConfig={"Delay": 5, "MaxAttempts": 60},
                )
                self.report["cleanup"]["worker_state"] = "stopped"
        except Exception as error:
            self.report["errors"].append(f"Worker cleanup: {error}")
        if self.report["native"]:
            try:
                with self.stage("confirm_artifacts_retained"):
                    receipt = json.loads((self.artifacts / "native-result.json").read_text())
                    for name in ("support.FCStd", "support.step", "preview.png", "checks.json"):
                        record = receipt["artifacts"][name]
                        data = read_object(s3, outputs["ArtifactBucket"], record["key"], record["version_id"])
                        if digest(data) != record["sha256"]:
                            raise ValueError(f"Retained S3 artifact hash mismatch: {name}")
                    self.report["cleanup"]["artifacts_retained"] = True
            except Exception as error:
                self.report["errors"].append(f"Retained evidence: {error}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--deploy", action="store_true", help="Create a new billable sample stack first")
    mode.add_argument("--existing-stack", action="store_true", help="Test and stop an existing matching sample worker")
    parser.add_argument("--example", type=Path, default=ROOT / "examples/support-bracket.json")
    parser.add_argument("--profile")
    parser.add_argument("--region", choices=("us-east-1",), default="us-east-1")
    parser.add_argument("--account", required=True)
    parser.add_argument("--stack", required=True)
    parser.add_argument("--hours", type=int, choices=range(1, 25), default=4)
    parser.add_argument("--model", default="openai.gpt-6.1-sol")
    parser.add_argument("--vpc-id")
    parser.add_argument("--subnet-id")
    args = parser.parse_args()
    if not re.fullmatch(r"\d{12}", args.account):
        parser.error("--account must be a 12-digit account ID")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,31}", args.stack):
        parser.error("--stack must be a letter followed by at most 31 letters, digits or hyphens")
    if bool(args.vpc_id) != bool(args.subnet_id):
        parser.error("--vpc-id and --subnet-id must be supplied together")
    if args.existing_stack and (args.vpc_id or args.subnet_id):
        parser.error("Network arguments apply only to --deploy")
    example = load_example(args.example)
    return Run(args, example).execute()


if __name__ == "__main__":
    sys.exit(main())
