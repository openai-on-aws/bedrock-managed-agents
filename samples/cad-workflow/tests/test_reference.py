from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import unittest

from jsonschema import ValidationError

from cad_workflow.example import ROOT, validate_document
from evidence import verify_reference


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "examples/support-bracket/reference"
        self.reference = verify_reference(self.directory)

    def report(self):
        reference = self.reference
        names = (
            "identity", "worker_provenance", "bma_and_native", "native_audit",
            "verify_evidence", "cloudwatch_export", "delete_session", "stop_worker",
            "confirm_stopped", "confirm_artifacts_retained",
        )
        return {
            "schema_version": "bma-cad/e2e-report/v1", "status": "passed",
            "example_id": reference["example_id"], "job_id": "a" * 32,
            "started_at": reference["verified_at"], "completed_at": reference["verified_at"],
            "scope": {"account": "111122223333", "region": "us-east-1",
                      "stack": "synthetic", "deployment_mode": "existing"},
            "source": reference["source"], "parameters": reference["parameters"],
            "stages": [{"name": name, "status": "passed", "started_at": reference["verified_at"],
                        "elapsed_seconds": 0} for name in names],
            "native": reference["native"],
            "bma": {**reference["bma"], "tools": [{**tool, "error": None} for tool in reference["bma"]["tools"]]},
            "audit": reference["audit"], "logs": reference["logs"],
            "cleanup": reference["cleanup"], "errors": [],
        }

    def test_checked_in_native_reference_verifies_offline(self):
        self.assertEqual(self.reference["status"], "passed")
        self.assertEqual(len(self.reference["native"]["checks"]), 11)

    def test_altered_reference_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "reference"
            shutil.copytree(self.directory, directory)
            (directory / "support.step").write_bytes(b"not the verified STEP")
            with self.assertRaisesRegex(ValueError, "Reference bytes"):
                verify_reference(directory)

    def test_missing_stage_cannot_be_hidden_by_duplicating_another_stage(self):
        report = self.report()
        validate_document(report, "e2e-report.schema.json")
        for name in ("native_audit", "cloudwatch_export", "confirm_stopped"):
            candidate = deepcopy(report)
            candidate["stages"] = [stage for stage in candidate["stages"] if stage["name"] != name]
            candidate["stages"].append(deepcopy(candidate["stages"][0]))
            with self.subTest(stage=name), self.assertRaises(ValidationError):
                validate_document(candidate, "e2e-report.schema.json")

    def test_unconfirmed_cleanup_cannot_be_marked_as_passed(self):
        report = deepcopy(self.report())
        report["cleanup"]["worker_state"] = "running"
        with self.assertRaises(ValidationError):
            validate_document(report, "e2e-report.schema.json")

    def test_deploy_mode_requires_a_successful_deploy_stage(self):
        report = self.report()
        report["scope"]["deployment_mode"] = "deploy"
        with self.assertRaises(ValidationError):
            validate_document(report, "e2e-report.schema.json")
        report["stages"].append({**report["stages"][0], "name": "deploy"})
        validate_document(report, "e2e-report.schema.json")
