"""Machine-readable example inputs and checks over saved end-to-end evidence."""

import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from cad_workflow.backends import get_backend
from cad_workflow.contract import (
    REQUIRED_CHECKS, digest, json_bytes, request, require_verified_report,
)

ROOT = Path(__file__).resolve().parents[1]
AUDIT_CASES = {
    "control": None,
    "through-pocket": "blind_pocket",
    "thin-floor": "residual_floor",
    "shifted-bore": "material_and_mounting_interfaces",
    "stale-step": "step_roundtrip",
}


def validate_document(value, schema_name):
    schema = json.loads((ROOT / "schemas" / schema_name).read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    # JSON numbers must also be finite; Python's decoder accepts NaN by default.
    json_bytes(value)


def load_example(path):
    value = json.loads(Path(path).read_text())
    validate_document(value, "example.schema.json")
    parameters = request({"job_id": "0" * 32, **value["parameters"]})
    floor = parameters["stock_thickness_mm"] - parameters["pocket_depth_mm"]
    if not math.isclose(floor, value["expected"]["floor_mm"], abs_tol=1e-7):
        raise ValueError("Example's expected floor does not match thickness minus depth")
    if set(value["expected"]["required_checks"]) != REQUIRED_CHECKS:
        raise ValueError("Example must require the complete native check contract")
    return value


def verify_saved_run(example, directory, audit_path):
    """Recheck native bytes, both BMA tools, dimensions, and the semantic audit."""
    backend = get_backend(example["backend"])
    parameters = request({"job_id": directory.name, **example["parameters"]})
    result = json.loads((directory / "native-result.json").read_text())
    if result.get("parameters") != parameters or result.get("accepted") is not True:
        raise ValueError("Native result did not accept this exact example")
    if result.get("request_sha256") != digest(json_bytes(parameters)):
        raise ValueError("Native result has the wrong request hash")
    files = result["artifacts"]
    for name in (*backend.CANDIDATE_FILES, "checks.json"):
        data = (directory / name).read_bytes()
        record = files[name]
        if record["sha256"] != digest(data) or record["bytes"] != len(data):
            raise ValueError(f"Saved artifact does not match its S3 receipt: {name}")
        if record["key"] != f"jobs/{directory.name}/{name}" or not record.get("version_id"):
            raise ValueError("Receipt does not identify this job's versioned artifact")
    checks = json.loads((directory / "checks.json").read_text())
    if result["checks"] != checks:
        raise ValueError("Saved checks differ from the native receipt")
    require_verified_report(
        checks, backend.NAME,
        {name: digest((directory / name).read_bytes()) for name in backend.CHECKED_FILES},
    )
    floor = next(check["measured_mm"] for check in checks["checks"] if check["name"] == "residual_floor")
    if not math.isclose(floor, example["expected"]["floor_mm"], abs_tol=1e-7):
        raise ValueError("Measured floor differs from the example's expectation")
    receipt = json.loads((directory / "bma-receipt.json").read_text())
    if not receipt["turns"] or receipt["turns"][0]["status"] != "completed":
        raise ValueError("BMA turn did not complete")
    items = json.loads((directory / "bma-items.json").read_text())
    tools = [{"name": item["name"], "status": item.get("status"), "error": item.get("error")}
             for item in items if item.get("type") == "mcp_call"]
    if any(item["status"] != "completed" or item["error"] for item in tools):
        raise ValueError("BMA recorded an incomplete or failed CAD tool call")
    successful = {item["name"].rsplit("___", 1)[-1] for item in tools
                  if item["status"] == "completed" and not item["error"]}
    if not {"create_support", "get_result"} <= successful:
        raise ValueError("Both CAD MCP tools must have completed successfully")
    audit = json.loads(audit_path.read_text())
    if audit.get("source_job_id") != directory.name or audit.get("passed") is not True:
        raise ValueError("Native audit did not pass for this job")
    if audit.get("verifier_sha256") != digest((ROOT / "cad_workflow/backends/freecad/verifier.py").read_bytes()):
        raise ValueError("Native audit used a different verifier source")
    if audit.get("audit_sha256") != digest((ROOT / "ops/freecad_audit.py").read_bytes()):
        raise ValueError("Native audit used different mutation-suite source")
    cases = audit.get("cases", [])
    if len(cases) != len(AUDIT_CASES) or {case["case"] for case in cases} != set(AUDIT_CASES):
        raise ValueError("Native audit did not cover the complete case set")
    for case in cases:
        expected = AUDIT_CASES[case["case"]]
        if case.get("passed") is not True:
            raise ValueError(f"Native audit failed: {case['case']}")
        if expected and expected not in case.get("failed_checks", []):
            raise ValueError(f"Audit did not reject the intended defect: {case['case']}")
        if expected is None and case.get("failed_checks"):
            raise ValueError("Native audit control contains failed checks")
    return {
        "native": checks,
        "bma": {"status": "completed", "model": receipt["model"],
                "elapsed_seconds": receipt["elapsed_seconds"], "tools": tools,
                "usage": receipt["turns"][0].get("usage")},
        "audit": {"passed": True, "elapsed_seconds": audit["elapsed_seconds"],
                  "verifier_sha256": audit["verifier_sha256"], "audit_sha256": audit["audit_sha256"],
                  "cases": [{"case": case["case"], "passed": case["passed"],
                             "failed_checks": case["failed_checks"]} for case in cases]},
    }
