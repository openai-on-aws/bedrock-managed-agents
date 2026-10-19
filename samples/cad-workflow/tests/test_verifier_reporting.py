import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cad_workflow.backends.freecad import verifier


class VerifierExitTests(unittest.TestCase):
    def run_failure(self, returned=None, error=None):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            request = directory / "request.json"
            request.write_text(json.dumps({"job_id": "a" * 32, "stock_thickness_mm": 14,
                                           "pocket_depth_mm": 12}))
            environment = {"CAD_REQUEST": str(request), "CAD_OUTPUT": temporary,
                           "CAD_SOURCE": temporary}
            with patch.dict(verifier.os.environ, environment), \
                 patch.object(verifier, "verify", return_value=returned, side_effect=error), \
                 patch.object(verifier.os, "_exit", side_effect=SystemExit) as exit_process:
                with self.assertRaises(SystemExit):
                    verifier.main()
                exit_process.assert_called_once_with(2)
            report = json.loads((directory / "checks.json").read_text())
            self.assertIs(report["accepted"], False)
            self.assertTrue((directory / "verifier-error.txt").read_text())

    def test_nonfinite_native_measurement_rejects_and_exits(self):
        self.run_failure(returned={"accepted": True, "measured_floor_mm": float("-inf")})

    def test_native_exception_rejects_and_exits(self):
        self.run_failure(error=RuntimeError("Synthetic native geometry failure"))
