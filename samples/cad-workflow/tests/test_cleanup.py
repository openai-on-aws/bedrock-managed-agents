import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cleanup


class CleanupTests(unittest.TestCase):
    def test_session_cleanup_is_bound_to_the_recorded_job_and_aws_scope(self):
        identifier = "a" * 32
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "artifacts" / identifier
            directory.mkdir(parents=True)
            (directory / "aws-context.json").write_text(json.dumps({
                "account": "111122223333", "region": "us-east-1", "stack": "example",
            }))
            (directory / "bma-session.json").write_text('{"id":"sess_synthetic"}')
            with patch.object(cleanup, "ROOT", root):
                self.assertEqual(
                    cleanup.session_record(identifier, "111122223333", "us-east-1", "example"),
                    (directory, "sess_synthetic"),
                )
                for scope in (("999900001111", "us-east-1", "example"),
                              ("111122223333", "us-west-2", "example"),
                              ("111122223333", "us-east-1", "another-stack")):
                    with self.subTest(scope=scope), self.assertRaises(ValueError):
                        cleanup.session_record(identifier, *scope)
                with self.assertRaises(ValueError):
                    cleanup.session_record("../other", "111122223333", "us-east-1", "example")
