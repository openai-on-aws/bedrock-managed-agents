#!/usr/bin/env python3
"""Verify saved evidence offline or export a portable reference from a passed run."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from cad_workflow.contract import digest, require_verified_report
from cad_workflow.example import (
    ROOT, load_example, validate_document, verify_saved_run,
)
from run import save

REFERENCE_FILES = ("support.FCStd", "support.step", "preview.png", "checks.json")


def verify_run(report_path, example_path):
    report = json.loads(report_path.read_text())
    validate_document(report, "e2e-report.schema.json")
    if report["status"] != "passed":
        raise ValueError("The end-to-end report did not pass")
    example = load_example(example_path)
    if report["example_id"] != example["id"] or report["parameters"] != example["parameters"]:
        raise ValueError("Report does not describe this example")
    if report["source"]["example_sha256"] != digest(example_path.read_bytes()):
        raise ValueError("Example changed since the run")
    directory = ROOT / "artifacts" / report["job_id"]
    audits = list(directory.glob("audit-*/audit.json"))
    if len(audits) != 1:
        raise ValueError("Exactly one audit report is required")
    actual = verify_saved_run(example, directory, audits[0])
    for key, value in actual.items():
        if value != report[key]:
            raise ValueError(f"Saved {key} evidence differs from the end-to-end report")
    return report, directory


def export_reference(report, directory, destination):
    # Construct an explicit public record. Never copy session transcripts,
    # deployment outputs, account IDs, resource ARNs, S3 locations or local paths.
    reference = {
        "schema_version": "bma-cad/reference/v1", "status": "passed",
        "example_id": report["example_id"], "verified_at": report["completed_at"],
        "region": report["scope"]["region"], "parameters": report["parameters"],
        "timings": {
            "e2e_seconds": round((datetime.fromisoformat(report["completed_at"])
                                  - datetime.fromisoformat(report["started_at"])).total_seconds(), 3),
            "native_seconds": json.loads((directory / "native-result.json").read_text())["elapsed_seconds"],
            "deployment_included": report["scope"]["deployment_mode"] == "deploy",
        },
        "source": report["source"], "native": report["native"],
        "bma": {
            "model": report["bma"]["model"], "status": report["bma"]["status"],
            "elapsed_seconds": report["bma"]["elapsed_seconds"],
            "tools": [{"name": item["name"], "status": item["status"]} for item in report["bma"]["tools"]],
            "usage_reported": report["bma"]["usage"] is not None,
        },
        "audit": report["audit"], "logs": report["logs"], "cleanup": report["cleanup"],
        "files": {},
    }
    content = {name: (directory / name).read_bytes() for name in REFERENCE_FILES}
    reference["files"] = {name: {"sha256": digest(data), "bytes": len(data)}
                          for name, data in content.items()}
    validate_document(reference, "reference.schema.json")
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in content.items():
        (destination / name).write_bytes(data)
    save(destination / "verified-example.json", reference)
    return reference


def verify_reference(directory):
    reference = json.loads((directory / "verified-example.json").read_text())
    validate_document(reference, "reference.schema.json")
    for name in REFERENCE_FILES:
        data = (directory / name).read_bytes()
        record = reference["files"][name]
        if digest(data) != record["sha256"] or len(data) != record["bytes"]:
            raise ValueError(f"Reference bytes do not match the manifest: {name}")
    checks = json.loads((directory / "checks.json").read_text())
    if checks != reference["native"]:
        raise ValueError("Reference check report differs from its manifest")
    require_verified_report(
        checks, "freecad",
        {name: digest((directory / name).read_bytes()) for name in ("support.FCStd", "support.step")},
    )
    return reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify-run", "export-reference", "verify-reference"))
    parser.add_argument("--report", type=Path, help="Private .state/e2e/<job-id>/report.json")
    parser.add_argument("--example", type=Path, default=ROOT / "examples/support-bracket.json")
    parser.add_argument("--directory", type=Path, default=ROOT / "examples/support-bracket/reference")
    args = parser.parse_args()
    if args.action == "verify-reference":
        value = verify_reference(args.directory)
    else:
        if not args.report:
            parser.error("--report is required for saved-run verification or reference export")
        value, directory = verify_run(args.report, args.example)
        if args.action == "export-reference":
            value = export_reference(value, directory, args.directory)
    print(json.dumps({"event": "evidence_verified", "action": args.action,
                      "status": value["status"], "example_id": value["example_id"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
