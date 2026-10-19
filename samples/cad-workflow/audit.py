#!/usr/bin/env python3
"""Run the controlled native grader audit on this deployment's own worker."""

import argparse
import base64
import json
from pathlib import Path
import time
import uuid

import boto3
from botocore.config import Config

from cad_workflow.contract import job_id
from run import read_object, save, stack_outputs

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="AWS profile; otherwise use the standard AWS credential chain")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--account", required=True)
    parser.add_argument("--stack", default="bma-cad-freecad")
    parser.add_argument("--source-job", required=True)
    args = parser.parse_args()
    identifier = job_id(args.source_job)
    suite_id = uuid.uuid4().hex
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    config = Config(connect_timeout=5, read_timeout=20,
                    retries={"total_max_attempts": 2, "mode": "standard"})
    if session.client("sts", config=config).get_caller_identity()["Account"] != args.account:
        raise ValueError("Authenticated account does not match --account")
    cfn = session.client("cloudformation", config=config)
    outputs = stack_outputs(cfn, args.stack)
    if outputs["CadBackend"] != "freecad":
        raise ValueError("This native mutation suite is implemented only for the FreeCAD backend")
    files = {
        "freecad_audit.py": (ROOT / "ops/freecad_audit.py").read_text(),
        "mutant.FCMacro": (
            "import sys\nsys.path.insert(0, '/opt/cad-workflow')\n"
            "from freecad_audit import mutate\nmutate()\n"),
        "run_audit.py": (
            "import sys\nsys.path.insert(0, '/opt/cad-workflow')\n"
            "from freecad_audit import audit\n"
            "raise SystemExit(audit(sys.argv[1], sys.argv[2]))\n"),
    }
    bundle = base64.b64encode(json.dumps(files).encode()).decode()
    script = (
        "set -eu\n"
        "python3 - <<'PY'\n"
        "import base64, json\nfrom pathlib import Path\n"
        f"files=json.loads(base64.b64decode('{bundle}'))\n"
        "for name,text in files.items():\n"
        "    path=Path('/opt/cad-workflow')/name\n"
        "    path.write_text(text)\n    path.chmod(0o644)\n"
        "PY\n"
        ". /etc/cad-workflow.env\n"
        f"/opt/cad-control/bin/python -I -B /opt/cad-workflow/run_audit.py {identifier} {suite_id}\n"
    )
    ssm = session.client("ssm", config=config)
    s3 = session.client("s3", config=config)
    command = ssm.send_command(
        InstanceIds=[outputs["WorkerId"]], DocumentName="AWS-RunShellScript",
        Parameters={"commands": [script], "executionTimeout": ["1200"]},
        TimeoutSeconds=1200, Comment=f"Controlled FreeCAD grader audit {suite_id}",
    )["Command"]["CommandId"]
    directory = ROOT / "artifacts" / identifier / f"audit-{suite_id}"
    save(directory / "invocation.json", {"suite_id": suite_id, "command_id": command,
                                        "worker_id": outputs["WorkerId"]})
    print(json.dumps({"event": "audit_started", "suite_id": suite_id, "command_id": command}), flush=True)
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        try:
            result = ssm.get_command_invocation(CommandId=command, InstanceId=outputs["WorkerId"])
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(3)
            continue
        if result["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
            save(directory / "ssm-result.json", result)
            try:
                report = json.loads(read_object(s3, outputs["ArtifactBucket"], f"health/audits/{suite_id}/audit.json"))
            except s3.exceptions.NoSuchKey as error:
                raise RuntimeError(f"Audit ended in {result['Status']} before producing a report. "
                                   f"Inspect {directory / 'ssm-result.json'}") from error
            save(directory / "audit.json", report)
            print(json.dumps({"event": "audit_completed", "passed": report["passed"],
                              "cases": [{"case": case["case"], "passed": case["passed"],
                                         "failed_checks": case["failed_checks"]} for case in report["cases"]],
                              "report": str(directory / "audit.json")}), flush=True)
            if result["Status"] != "Success" or report.get("passed") is not True:
                raise RuntimeError("Native grader audit failed")
            return
        time.sleep(5)
    raise TimeoutError("Native audit exceeded its 20-minute SSM execution limit")


if __name__ == "__main__":
    main()
