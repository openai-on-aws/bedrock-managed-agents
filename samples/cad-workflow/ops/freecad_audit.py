"""Operator-only native grader audit. This is never exposed as an agent tool."""

import json
import os
from pathlib import Path
import traceback


def mutate():
    """Run in FreeCAD and alter a valid candidate in controlled, semantic ways."""
    import FreeCAD as App
    import Part

    destination = Path(os.environ["CAD_OUTPUT"])
    case = destination.parent.name
    source = Path(os.environ["CAD_SOURCE"])
    status = 0
    try:
        document = App.openDocument(str(source / "support.FCStd"))
        pocket = document.getObject("LighteningPocket")
        if case == "through-pocket":
            pocket.Type = 1
        elif case == "thin-floor":
            pocket.Length = document.getObject("StockPad").Length.Value - 1
        elif case == "shifted-bore":
            bores = document.getObject("BoreSketch")
            index = next(i for i, constraint in enumerate(bores.Constraints)
                         if constraint.Type == "DistanceX" and constraint.First == 0)
            bores.setDatum(index, App.Units.Quantity("16 mm"))
        elif case not in ("control", "stale-step"):
            raise ValueError("Unknown controlled mutation")
        document.recompute()
        document.saveAs(str(destination / "support.FCStd"))
        if case == "stale-step":
            Part.makeBox(160, 42, 14).exportStep(str(destination / "support.step"))
        else:
            Part.export([document.getObject("Support")], str(destination / "support.step"))
    except Exception:
        (destination / "mutation-error.txt").write_text(traceback.format_exc())
        status = 2
    os._exit(status)


def audit(source_job, suite_id):
    import fcntl
    import time

    import boto3
    from botocore.config import Config
    from cad_workflow.contract import digest, job_id, json_bytes
    from cad_workflow.worker import CODE, ROOT, phase, read_regular, write_new

    source_job, suite_id = job_id(source_job), job_id(suite_id)
    source = ROOT / "jobs" / source_job
    original = json.loads(read_regular(source / "result.json"))
    if not original.get("accepted"):
        raise ValueError("Audit requires an accepted baseline job")
    request_path = source / "request.json"
    suites = ROOT / "audits"
    suites.mkdir(exist_ok=True, mode=0o755)
    directory = suites / suite_id
    directory.mkdir(mode=0o755)
    expected = {
        "control": None,
        "through-pocket": "blind_pocket",
        "thin-floor": "residual_floor",
        "shifted-bore": "material_and_mounting_interfaces",
        "stale-step": "step_roundtrip",
    }
    cases = []
    started = time.monotonic()
    with (ROOT / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for index, (name, failed_check) in enumerate(expected.items()):
            case = directory / name
            case.mkdir(mode=0o755)
            identifier = f"audit-{suite_id}-{index}"
            mutation_exit = phase(identifier, "mutant", case / "mutant",
                                  request_path, source / "frozen")
            if mutation_exit:
                raise RuntimeError(f"Failed to construct audit case {name}; inspect {case}")
            frozen = case / "frozen"
            frozen.mkdir(mode=0o755)
            for filename in ("support.FCStd", "support.step"):
                write_new(frozen / filename, read_regular(case / "mutant" / filename))
            frozen.chmod(0o555)
            verifier_exit = phase(identifier, "verifier", case / "verifier", request_path, frozen)
            report = json.loads(read_regular(case / "verifier" / "checks.json"))
            failures = [check["name"] for check in report.get("checks", [])
                        if not check.get("passed")]
            if name == "control":
                passed = verifier_exit == 0 and report.get("accepted") is True
            else:
                # A crash, missing file, or parser failure is not evidence that a
                # semantic grader rule rejected the intended defect.
                passed = (verifier_exit == 2 and report.get("accepted") is False
                          and failed_check in failures)
            cases.append({"case": name, "passed": passed, "expected_failed_check": failed_check,
                          "failed_checks": failures, "report": report})
    result = {"suite_id": suite_id, "source_job_id": source_job,
              "passed": all(case["passed"] for case in cases), "cases": cases,
              "elapsed_seconds": round(time.monotonic() - started, 3),
              "verifier_sha256": digest((CODE / "cad_workflow/backends/freecad/verifier.py").read_bytes()),
              "audit_sha256": digest(Path(__file__).read_bytes())}
    write_new(directory / "audit.json", json_bytes(result))
    session = boto3.Session(region_name=os.environ.get("AWS_REGION"))
    client = session.client("s3", config=Config(
        connect_timeout=5, read_timeout=20, retries={"total_max_attempts": 2, "mode": "standard"}))
    bucket = os.environ["CAD_BUCKET"]
    prefix = f"health/audits/{suite_id}"
    for path in sorted(directory.rglob("*")):
        if path.is_file() and (path.name in ("checks.json", "support.FCStd", "support.step", "audit.json")
                               or path.name.endswith("-error.txt")):
            data = read_regular(path)
            client.put_object(Bucket=bucket, Key=f"{prefix}/{path.relative_to(directory)}",
                              Body=data, IfNoneMatch="*", ServerSideEncryption="AES256",
                              Metadata={"sha256": digest(data)})
    print(json.dumps({"suite_id": suite_id, "passed": result["passed"], "key": f"{prefix}/audit.json"}))
    return 0 if result["passed"] else 2
