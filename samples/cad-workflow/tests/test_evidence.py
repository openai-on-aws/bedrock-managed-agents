from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from cad_workflow.backends import get_backend
from cad_workflow.contract import (
    REQUIRED_CHECKS, SCHEMA, digest, json_bytes, request, require_verified_report,
)
from run import download


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.parameters = request({
            "job_id": "a" * 32, "stock_thickness_mm": 14, "pocket_depth_mm": 12,
        })
        # Synthetic bytes test transport integrity, not native geometry.
        self.files = {
            "support.FCStd": b"synthetic-native", "support.step": b"synthetic-step",
            "preview.png": b"synthetic-image", "author.json": b"{}",
        }
        self.hashes = {name: digest(self.files[name]) for name in get_backend().CHECKED_FILES}
        self.report = {
            "schema": SCHEMA, "backend": "freecad", "accepted": True,
            "checks": [{"name": name, "passed": True} for name in sorted(REQUIRED_CHECKS)],
            "input_sha256": self.hashes,
        }
        self.files["checks.json"] = json_bytes(self.report)
        self.receipt = {
            "schema": SCHEMA, "backend": "freecad", "job_id": self.parameters["job_id"],
            "parameters": self.parameters, "request_sha256": digest(json_bytes(self.parameters)),
            "status": "accepted", "accepted": True, "checks": self.report,
            "artifacts": {
                name: {"key": f"jobs/{self.parameters['job_id']}/{name}",
                       "sha256": digest(data), "bytes": len(data), "version_id": f"version-{name}"}
                for name, data in self.files.items()
            },
        }

    def client(self, receipt, files=None):
        files = self.files if files is None else files
        client = Mock()
        bodies = []

        def get_object(**kwargs):
            name = kwargs["Key"].rsplit("/", 1)[-1]
            if name == "result.json":
                data = json_bytes(receipt)
            else:
                self.assertEqual(kwargs["VersionId"], receipt["artifacts"][name]["version_id"])
                data = files[name]
            stream = io.BytesIO(data)
            bodies.append(stream)
            return {"ContentLength": len(data), "Body": stream}

        client.get_object.side_effect = get_object
        return client, bodies

    def download(self, receipt, files=None):
        client, bodies = self.client(receipt, files)
        with tempfile.TemporaryDirectory() as directory:
            result = download(client, {"ArtifactBucket": "test-bucket", "CadBackend": "freecad"},
                              self.parameters, Path(directory))
            self.assertTrue((Path(directory) / "native-result.json").is_file())
        self.assertTrue(all(body.closed for body in bodies))
        return result

    def test_accepts_complete_report_and_versioned_matching_bytes(self):
        self.assertIs(self.download(self.receipt)["accepted"], True)

    def test_missing_duplicate_or_failed_checks_cannot_pass(self):
        cases = []
        missing = deepcopy(self.report)
        missing["checks"].pop()
        cases.append(missing)
        duplicate = deepcopy(self.report)
        duplicate["checks"][0] = duplicate["checks"][1]
        cases.append(duplicate)
        failed = deepcopy(self.report)
        failed["checks"][0]["passed"] = False
        cases.append(failed)
        wrong_hash = deepcopy(self.report)
        wrong_hash["input_sha256"]["support.FCStd"] = "0" * 64
        cases.append(wrong_hash)
        wrong_backend = deepcopy(self.report)
        wrong_backend["backend"] = "catia-v6"
        cases.append(wrong_backend)
        for report in cases:
            with self.subTest(report=report), self.assertRaises(ValueError):
                require_verified_report(report, "freecad", self.hashes)

    def test_modified_downloaded_bytes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "hash or size"):
            self.download(self.receipt, {**self.files, "support.FCStd": b"changed"})

    def test_foreign_job_artifact_path_or_unversioned_object_is_rejected(self):
        for field, value in (("key", "jobs/other/support.FCStd"), ("version_id", "")):
            receipt = deepcopy(self.receipt)
            receipt["artifacts"]["support.FCStd"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.download(receipt)

    def test_accepted_receipt_cannot_omit_native_files(self):
        receipt = deepcopy(self.receipt)
        receipt["artifacts"].pop("support.FCStd")
        with self.assertRaisesRegex(ValueError, "missing native artifacts"):
            self.download(receipt)

    def test_receipt_cannot_overwrite_client_history(self):
        receipt = deepcopy(self.receipt)
        receipt["artifacts"]["bma-session.json"] = receipt["artifacts"]["author.json"]
        with self.assertRaisesRegex(ValueError, "unexpected artifacts"):
            self.download(receipt)

    def test_receipt_cannot_substitute_a_different_check_report(self):
        receipt = deepcopy(self.receipt)
        receipt["checks"]["checks"][0]["passed"] = False
        with self.assertRaisesRegex(ValueError, "differs from"):
            self.download(receipt)
