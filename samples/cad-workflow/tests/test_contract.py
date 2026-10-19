import unittest

from cad_workflow.contract import request


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.valid = {
            "job_id": "a" * 32,
            "stock_thickness_mm": 14,
            "pocket_depth_mm": 12,
        }

    def test_blind_pocket_at_minimum_floor_is_admitted(self):
        self.assertEqual(request(self.valid)["pocket_depth_mm"], 12.0)

    def test_through_pocket_and_insufficient_floor_are_refused(self):
        for depth in (12.001, 13, 14, 15, -1, 0):
            with self.subTest(depth=depth), self.assertRaises(ValueError):
                request({**self.valid, "pocket_depth_mm": depth})

    def test_nonfinite_boolean_and_text_dimensions_are_refused(self):
        for value in (True, False, float("nan"), float("inf"), "12", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                request({**self.valid, "pocket_depth_mm": value})

    def test_paths_and_shell_content_cannot_be_job_identifiers(self):
        for value in ("../" * 8, "/tmp/result", "a" * 32 + ";id", "A" * 32, ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                request({**self.valid, "job_id": value})

    def test_unknown_fields_and_missing_dimensions_are_refused(self):
        with self.assertRaises(ValueError):
            request({**self.valid, "python": "print('untrusted')"})
        with self.assertRaises(ValueError):
            request({"job_id": "a" * 32})
