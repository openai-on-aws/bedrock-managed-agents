from argparse import Namespace
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ProfileNotFound

from cad_workflow.example import ROOT, load_example, validate_document
from e2e import Run


class E2EFailureTests(unittest.TestCase):
    def new_run(self):
        example_path = ROOT / "examples/support-bracket.json"
        args = Namespace(example=example_path, profile="synthetic", region="us-east-1",
                         account="111122223333", stack="synthetic", deploy=False)
        return Run(args, load_example(example_path))

    def test_missing_profile_finishes_with_a_failed_machine_readable_report(self):
        with patch.object(Run, "persist"), patch("e2e.emit"), \
             patch("e2e.boto3.Session", side_effect=ProfileNotFound(profile="synthetic")), \
             patch.object(Run, "cleanup") as cleanup:
            run = self.new_run()
            self.assertEqual(run.execute(), 1)
            cleanup.assert_not_called()
        self.assertEqual(run.report["status"], "failed")
        self.assertEqual(run.report["stages"][0]["status"], "failed")
        self.assertIn("ProfileNotFound", run.report["errors"][0])
        validate_document(run.report, "e2e-report.schema.json")

    def test_wrong_account_fails_before_deployment_or_worker_cleanup(self):
        session = Mock()
        session.client.return_value.get_caller_identity.return_value = {"Account": "999900001111"}
        with patch.object(Run, "persist"), patch("e2e.emit"), \
             patch("e2e.boto3.Session", return_value=session), \
             patch.object(Run, "command") as command, patch.object(Run, "cleanup") as cleanup:
            run = self.new_run()
            run.args.deploy = True
            self.assertEqual(run.execute(), 1)
            command.assert_not_called()
            cleanup.assert_not_called()
        validate_document(run.report, "e2e-report.schema.json")
