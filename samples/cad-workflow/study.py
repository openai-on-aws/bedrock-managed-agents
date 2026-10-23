#!/usr/bin/env python3
"""Start, continue and collect a persistent BMA CAD design study."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path, PurePosixPath
import re
import sys
import time
import uuid

import boto3
from botocore.config import Config
from openai import OpenAI
from openai.providers import bedrock

from cad_workflow.backends import get_backend
from cad_workflow.contract import digest, job_id
from cad_workflow.example import validate_document
from cad_workflow.infrastructure import render
from cad_workflow.study import (
    compare, load_brief, message, proposals, recommendation, round_items, text_content, validate_selection,
)
from run import ROOT, download, output, read_object, save, stack_outputs

STATE = ROOT / ".state" / "studies"
PROMPT = ROOT / "prompts/design-study.md"


def now():
    return datetime.now(timezone.utc).isoformat()


def persist(directory, state):
    temporary = directory / "study.json.tmp"
    save(temporary, state)
    temporary.replace(directory / "study.json")


def client_sources():
    return {name: digest((ROOT / name).read_bytes()) for name in (
        "study.py", "cad_workflow/study.py", "prompts/design-study.md",
        "schemas/design-brief.schema.json", "schemas/study-report.schema.json")}


def known_candidates(state):
    return {row["job_id"]: row for record in state["rounds"] if record["collected"]
            for row in record["report"]["candidates"]}


@contextmanager
def locked(directory):
    with (directory / ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def environment(args):
    if not args.runtime_arn:
        return {"type": "none"}
    prefix = f"arn:aws:bedrock-agentcore:{args.region}:{args.account}:runtime/"
    if not args.runtime_arn.startswith(prefix) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", args.runtime_arn[len(prefix):]
    ):
        raise ValueError("Runtime must be an ARN in the selected account and region")
    role_prefix = f"arn:aws:iam::{args.account}:role/"
    if not args.session_role_arn.startswith(role_prefix):
        raise ValueError("Session role must belong to the selected account")
    paths = [args.workspace_directory, *(args.capability_directory or [])]
    if any(not PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts for path in paths):
        raise ValueError("Workspace and capability directories must be absolute environment paths")
    return {
        "type": "aws_bedrock_agentcore", "runtime_arn": args.runtime_arn,
        "runtime_qualifier": args.runtime_qualifier,
        "workspace_directory": args.workspace_directory,
        "capability_directories": args.capability_directory or [],
    }


def aws_session(args):
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    config = Config(connect_timeout=5, read_timeout=30,
                    retries={"total_max_attempts": 2, "mode": "standard"})
    if session.client("sts", config=config).get_caller_identity()["Account"] != args.account:
        raise ValueError("Authenticated account does not match --account")
    return session, config


def check_worker(session, config, args, needs_compute=True):
    outputs = stack_outputs(session.client("cloudformation", config=config), args.stack)
    if outputs["CadBackend"] != "freecad":
        raise ValueError("The design-study example requires the FreeCAD backend")
    if outputs["SourceSha256"] != render()["Metadata"]["SourceSha256"]:
        raise ValueError("Worker source differs from this checkout")
    bootstrap = json.loads(read_object(
        session.client("s3", config=config), outputs["ArtifactBucket"], "health/bootstrap.json"))
    if bootstrap["source_sha256"] != outputs["SourceSha256"]:
        raise ValueError("Worker bootstrap source differs from the deployed source")
    if not needs_compute:
        return outputs
    worker = session.client("ec2", config=config).describe_instances(
        InstanceIds=[outputs["WorkerId"]])["Reservations"][0]["Instances"][0]
    if worker["State"]["Name"] != "running":
        raise ValueError("Worker must be running; see the walkthrough before restarting it")
    deadline = datetime.fromisoformat(outputs["StopAtUtc"]).replace(tzinfo=timezone.utc)
    if (deadline - datetime.now(timezone.utc)).total_seconds() < 900:
        raise ValueError("Worker needs at least 15 minutes before its scheduled stop")
    return outputs


def verify_scope(state, args):
    expected = {"account": args.account, "region": args.region, "stack": args.stack}
    if state["scope"] != expected:
        raise ValueError("Study belongs to a different account, region or stack")
    if state["closed"]:
        raise ValueError("Study session has been deleted")


def remote_snapshot(client, directory, state):
    session = client.beta.agents.sessions.retrieve(state["session_id"]).model_dump(
        mode="json", warnings=False)
    actual = session["environment"]
    if any(actual.get(key) != value for key, value in state["environment"].items()):
        raise ValueError("Remote session environment differs from the saved study")
    if session.get("role_arn") != state["session_role_arn"]:
        raise ValueError("Remote session role differs from the saved study")
    tools = session["agent"].get("tools") or []
    if not any(tool.get("connector_id") == state["outputs"]["GatewayArn"] for tool in tools):
        raise ValueError("Remote session does not use the study's CAD Gateway")
    items = [item.model_dump(mode="json", warnings=False)
             for item in client.beta.agents.sessions.items.list(
                 state["session_id"], order="asc", limit=100)]
    save(directory / "session.json", session)
    save(directory / "items.json", items)
    return session, items


def recover_session(client, directory, state):
    """Resolve an ambiguous create using the service's durable input marker."""
    if state["session_id"]:
        _, items = remote_snapshot(client, directory, state)
        round_items(items, state["rounds"][-1]["id"], state["rounds"][-1]["input_sha256"])
        if not state["rounds"][-1]["collected"]:
            state["rounds"][-1]["status"] = "submitted"
        persist(directory, state)
        output({"event": "study_recovered", "study_id": state["id"], "session_id": state["session_id"]})
        return
    earliest = datetime.fromisoformat(state["created_at"]).timestamp() - 60
    matches = []
    for candidate in client.beta.agents.sessions.list(order="desc", limit=100):
        remote = candidate.model_dump(mode="json", warnings=False)
        if remote["created_at"] < earliest:
            continue
        if remote.get("role_arn") != state["session_role_arn"]:
            continue
        if remote["agent"].get("model") != state["outputs"]["ModelId"]:
            continue
        if any(remote["environment"].get(key) != value for key, value in state["environment"].items()):
            continue
        items = [item.model_dump(mode="json", warnings=False)
                 for item in client.beta.agents.sessions.items.list(remote["id"], order="asc", limit=100)]
        try:
            round_items(items, state["rounds"][0]["id"], state["rounds"][0]["input_sha256"])
        except ValueError:
            continue
        matches.append((remote, items))
    if len(matches) != 1:
        raise ValueError(f"Expected one matching durable session; found {len(matches)}. "
                         "No input was resubmitted. Inspect BMA state before creating another study.")
    remote, items = matches[0]
    state["session_id"] = remote["id"]
    state["rounds"][0]["status"] = "submitted"
    state["recovered_at"] = now()
    save(directory / "session-recovered.json", remote)
    save(directory / "items.json", items)
    persist(directory, state)
    output({"event": "study_recovered", "study_id": state["id"], "session_id": state["session_id"]})


def new_round(directory, state, brief):
    if len(state["rounds"]) >= 5:
        raise ValueError("This sample supports at most five review rounds per study")
    if not state["rounds"] and brief["candidate_count"] < 2:
        raise ValueError("The initial study needs slots for a baseline and an alternative")
    if state["rounds"] and (
        brief["id"] != state["rounds"][0]["brief"]["id"]
        or brief["baseline"] != state["rounds"][0]["brief"]["baseline"]
    ):
        raise ValueError("A continuation must retain the study ID and original baseline")
    identifier = uuid.uuid4().hex
    record = {
        "id": identifier, "number": len(state["rounds"]) + 1,
        "brief": brief, "candidate_job_ids": [uuid.uuid4().hex for _ in range(brief["candidate_count"])],
        "status": "submission_pending", "submitted_at": now(), "collected": False,
    }
    state["rounds"].append(record)
    payload = {
        "study_id": state["id"], "round": record["number"], "brief": brief,
        "candidate_job_ids": record["candidate_job_ids"],
        "measured_baseline_volume_mm3": state["baseline_volume_mm3"],
        "known_candidates": list(known_candidates(state).values()),
        "previous_report": state["rounds"][-2].get("report") if len(state["rounds"]) > 1 else None,
    }
    text = f"CAD_STUDY_ROUND={identifier}\n" + json.dumps(payload, allow_nan=False)
    record["input_sha256"] = digest(text.encode())
    save(directory / "rounds" / identifier / "input.json", payload)
    persist(directory, state)
    return record, text


def start(client, directory, args, session, config):
    brief = load_brief(args.brief)
    execution = environment(args)
    outputs = check_worker(session, config, args, needs_compute=brief["candidate_count"] > 0)
    state = {
        "schema_version": "bma-cad/study/v1", "id": directory.name, "created_at": now(),
        "scope": {"account": args.account, "region": args.region, "stack": args.stack},
        "environment": execution, "outputs": outputs, "closed": False, "session_id": None,
        "session_role_arn": args.session_role_arn or outputs["SessionRoleArn"],
        "baseline_volume_mm3": None, "rounds": [],
        "source_sha256": client_sources(),
    }
    record, text = new_round(directory, state, brief)
    try:
        created = client.beta.agents.sessions.create(
            environment=execution,
            agent={"model": outputs["ModelId"], "service_tier": "default",
                   "reasoning": {"effort": "medium"}, "instructions": PROMPT.read_text(),
                   "tools": [{"type": "mcp", "server_label": "cad",
                              "connector_id": outputs["GatewayArn"]}]},
            extra_body={"role_arn": state["session_role_arn"]},
            input=message(text)["input"], stream=False,
        )
    except Exception:
        record["status"] = "submission_unknown"
        persist(directory, state)
        # A transport error can happen after acceptance. Never retry a create
        # blindly: retain the round marker for reconciliation in the BMA API.
        raise
    state["session_id"] = created.id
    record["status"] = "submitted"
    save(directory / "session-created.json", created.model_dump(mode="json", warnings=False))
    persist(directory, state)
    output({"event": "study_submitted", "study_id": state["id"], "session_id": created.id,
            "round": record["number"], "state": str(directory / "study.json")})


def continue_study(client, directory, state, args, session, config):
    if not state["rounds"][-1]["collected"]:
        raise ValueError("Collect the previous round before submitting review feedback")
    if digest(PROMPT.read_bytes()) != state["source_sha256"]["prompts/design-study.md"]:
        raise ValueError("Study instructions changed; start a new session for the new prompt")
    brief = load_brief(args.brief)
    outputs = check_worker(session, config, args, needs_compute=brief["candidate_count"] > 0)
    for key in ("WorkerId", "ArtifactBucket", "GatewayArn", "SourceSha256", "ModelId"):
        if outputs[key] != state["outputs"][key]:
            raise ValueError("Deployment changed since this study started")
    remote, _ = remote_snapshot(client, directory, state)
    if remote["status"] != "idle":
        raise ValueError("Session must be idle before continuing")
    record, text = new_round(directory, state, brief)
    try:
        client.beta.agents.sessions.events.create(
            state["session_id"], events=[message(text)], idempotency_key=record["id"])
    except Exception:
        record["status"] = "submission_unknown"
        persist(directory, state)
        raise
    record["status"] = "submitted"
    persist(directory, state)
    output({"event": "study_submitted", "study_id": state["id"],
            "session_id": state["session_id"], "round": record["number"]})


def inspect(client, directory, state, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    record = state["rounds"][-1]
    previous = None
    while True:
        remote, items = remote_snapshot(client, directory, state)
        ready = False
        try:
            current = round_items(items, record["id"], record["input_sha256"])
            ready = any(item.get("phase") == "final_answer"
                        and item.get("status") == "completed" for item in current)
        except ValueError:
            current = []
        status = {"event": "study_status", "study_id": state["id"], "round": record["number"],
                  "session_status": remote["status"],
                  "ready_to_collect": remote["status"] == "idle" and ready,
                  "completed_tool_calls": sum(item.get("type") == "mcp_call"
                                              and item.get("status") == "completed" for item in current)}
        if status != previous:
            output(status)
            previous = status
        if remote["status"] != "in_progress" or time.monotonic() >= deadline:
            return
        time.sleep(min(5, max(0, deadline - time.monotonic())))


def write_report(directory, report):
    lines = [
        f"# {report['title']}", "",
        f"Round {report['round']} — geometric target met: **{report['goal_met']}**.",
        "Status: **awaiting engineer review**. This is not structural or manufacturing approval.", "",
        f"Measured baseline volume: {report['baseline_volume_mm3']:.3f} mm³.", "",
        "| Candidate | Evidence | Stock / pocket (mm) | Native checks | Floor (mm) | Volume (mm³) | Reduction | Meets brief |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(report["candidates"], 1):
        p = row["parameters"]
        label = f"[{index}](candidates/{row['job_id']}/native-result.json)"
        if row["native_accepted"]:
            measurements = f"{row['floor_mm']:g} | {row['volume_mm3']:.3f} | {row['volume_reduction_percent']:.2f}%"
        else:
            measurements = "— | — | —"
        lines.append(f"| {label} | {'new' if row['created_this_round'] else 'reused'} | "
                     f"{p['stock_thickness_mm']:g} / {p['pocket_depth_mm']:g} | "
                     f"{'passed' if row['native_accepted'] else 'failed'} | {measurements} | {row['meets_brief']} |")
    selected = report["recommendation"]["recommended_job_id"]
    if selected:
        lines += ["", f"[Recommended FreeCAD model](candidates/{selected}/support.FCStd) · "
                  f"[STEP export](candidates/{selected}/support.step)"]
    lines += ["", "## BMA's recommendation", "",
              *["> " + line for line in report["recommendation"]["rationale"].splitlines()], "",
              "[Machine-readable report](report.json)", ""]
    (directory / "report.md").write_text("\n".join(lines))


def collect(client, directory, state, session, config):
    record = state["rounds"][-1]
    if record["collected"]:
        output({"event": "study_collected", "study_id": state["id"],
                "report": record["report_path"], "goal_met": record["report"]["goal_met"]})
        return
    remote, all_items = remote_snapshot(client, directory, state)
    if remote["status"] != "idle":
        raise ValueError("Wait for an idle session before collecting the completed recommendation")
    items = round_items(all_items, record["id"], record["input_sha256"])
    submitted = json.loads(next(text_content(item) for item in items
                                if item.get("role") == "user").split("\n", 1)[1])
    if submitted["brief"] != record["brief"] or submitted["candidate_job_ids"] != record["candidate_job_ids"]:
        raise ValueError("Local brief or allocation differs from the input received by BMA")
    candidates = proposals(items, record["candidate_job_ids"], known_candidates(state),
                           minimum_new=2 if record["number"] == 1 else 0)
    decision = recommendation(items)
    round_directory = directory / "rounds" / record["id"]
    native_results = []
    s3 = session.client("s3", config=config)
    backend = get_backend("freecad")
    expected_source = {name: digest((ROOT / "cad_workflow" / name).read_bytes()) for name in (
        "contract.py", "backends/__init__.py", "worker.py", *backend.SOURCE_FILES)}
    for identifier, parameters in candidates.items():
        destination = round_directory / "candidates" / identifier
        destination.mkdir(parents=True, exist_ok=True)
        native = download(s3, state["outputs"], parameters, destination)
        if native["code_sha256"] != expected_source:
            raise ValueError("Executed native source differs from this checkout")
        native_results.append(native)
    if state["baseline_volume_mm3"] is None:
        baseline = record["brief"]["baseline"]
        controls = [native for native in native_results if native["accepted"]
                    and all(native["parameters"][key] == value for key, value in baseline.items())]
        if len(controls) != 1:
            raise ValueError("First round must include one accepted native baseline")
        state["baseline_volume_mm3"] = next(
            check["measured_mm3"] for check in controls[0]["checks"]["checks"]
            if check["name"] == "analytic_volume")
    rows = compare(record["brief"], native_results, state["baseline_volume_mm3"])
    for row in rows:
        row["created_this_round"] = row["job_id"] in record["candidate_job_ids"]
    goal_met = validate_selection(rows, decision["recommended_job_id"])
    commands = [item for item in items if item.get("type") == "command_execution"]
    report = {
        "schema_version": "bma-cad/study-report/v1", "status": "awaiting_review",
        "study_id": state["id"], "round": record["number"], "title": record["brief"]["title"],
        "collected_at": now(), "brief": record["brief"],
        "baseline_volume_mm3": state["baseline_volume_mm3"],
        "candidates": rows, "recommendation": decision, "goal_met": goal_met,
        "environment_type": state["environment"]["type"],
        "workspace_commands_observed": len(commands),
        "worker_sha256": state["outputs"]["SourceSha256"],
        "client_sha256": client_sources(), "session_client_sha256": state["source_sha256"],
    }
    validate_document(report, "study-report.schema.json")
    save(round_directory / "report.json", report)
    write_report(round_directory, report)
    record.update(collected=True, status="awaiting_review", report=report,
                  report_path=str(round_directory / "report.json"))
    persist(directory, state)
    output({"event": "study_collected", "study_id": state["id"], "round": record["number"],
            "goal_met": goal_met, "status": "awaiting_review",
            "report": record["report_path"], "readme": str(round_directory / "report.md")})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "continue", "status", "collect", "recover", "cancel", "delete-session"))
    parser.add_argument("--study-id")
    parser.add_argument("--brief", type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--region", choices=("us-east-1",), default="us-east-1")
    parser.add_argument("--account", required=True)
    parser.add_argument("--stack", required=True)
    parser.add_argument("--wait-seconds", type=int, default=0,
                        help="status only: wait up to this many seconds; never cancels the agent")
    parser.add_argument("--runtime-arn", help="start only: an existing BMA exec-server Runtime")
    parser.add_argument("--session-role-arn", help="start only: role allowing the model, Gateway and Runtime")
    parser.add_argument("--runtime-qualifier", default="DEFAULT")
    parser.add_argument("--workspace-directory", default="/mnt/home/workspace")
    parser.add_argument("--capability-directory", action="append")
    args = parser.parse_args()
    if not re.fullmatch(r"\d{12}", args.account):
        parser.error("--account must be a 12-digit account ID")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,31}", args.stack):
        parser.error("--stack must be a letter followed by at most 31 letters, digits or hyphens")
    if args.action != "start" and not args.study_id:
        parser.error("--study-id is required after start")
    if args.action in ("start", "continue") and not args.brief:
        parser.error("--brief is required for start and continue")
    if args.action not in ("start", "continue") and args.brief:
        parser.error("--brief applies only to start and continue")
    if bool(args.runtime_arn) != bool(args.session_role_arn):
        parser.error("--runtime-arn and --session-role-arn must be supplied together")
    if args.action != "start" and (args.runtime_arn or args.capability_directory):
        parser.error("Execution environment options apply only to start")
    if args.capability_directory and (not args.runtime_arn or len(set(args.capability_directory)) > 32):
        parser.error("Supply at most 32 unique capability paths with a Runtime")
    if not 0 <= args.wait_seconds <= 3600 or (args.wait_seconds and args.action != "status"):
        parser.error("--wait-seconds applies to status and must be between 0 and 3600")
    identifier = None
    directory = None
    try:
        identifier = job_id(args.study_id) if args.study_id else uuid.uuid4().hex
        directory = STATE / identifier
        if args.action == "start":
            load_brief(args.brief)
            environment(args)
            directory.mkdir(parents=True, exist_ok=False)
        with locked(directory):
            state = None
            if args.action != "start":
                state = json.loads((directory / "study.json").read_text())
                verify_scope(state, args)
                if not state["session_id"] and args.action != "recover":
                    raise ValueError("Session creation is unresolved; inspect saved state and BMA before retrying")
            session, config = aws_session(args)
            # Reuse the account-verified, refreshable credentials. Resolving
            # the profile again can invoke an external credential helper twice.
            with OpenAI(provider=bedrock(region=args.region, endpoint="mantle",
                                        credential_provider=session.get_credentials),
                        timeout=60, max_retries=0) as client:
                if args.action == "start":
                    start(client, directory, args, session, config)
                elif args.action == "continue":
                    continue_study(client, directory, state, args, session, config)
                elif args.action == "status":
                    inspect(client, directory, state, args.wait_seconds)
                elif args.action == "collect":
                    collect(client, directory, state, session, config)
                elif args.action == "recover":
                    recover_session(client, directory, state)
                else:
                    remote, _ = remote_snapshot(client, directory, state)
                    if args.action == "cancel":
                        client.beta.agents.sessions.events.create(
                            state["session_id"], events=[{"type": "agent.session.input.cancel"}])
                        output({"event": "study_cancel_requested", "study_id": identifier})
                    else:
                        if remote["status"] == "in_progress":
                            raise ValueError("Cancel and wait for the active turn before deleting its session")
                        client.beta.agents.sessions.delete(state["session_id"])
                        state.update(closed=True, deleted_at=now())
                        persist(directory, state)
                        output({"event": "study_session_deleted", "study_id": identifier,
                                "note": "Worker, Runtime and artifact storage are retained."})
        return 0
    except Exception as error:
        output({"event": "study_error", "error": f"{type(error).__name__}: {error}",
                "study_id": identifier, "state": str(directory / "study.json") if directory else None})
        return 1


if __name__ == "__main__":
    sys.exit(main())
