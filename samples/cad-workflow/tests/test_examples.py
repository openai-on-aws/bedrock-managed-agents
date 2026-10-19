from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import ValidationError

from cad_workflow.example import ROOT, load_example, validate_document
from cad_workflow.contract import REQUIRED_CHECKS


class ExampleTests(unittest.TestCase):
    def setUp(self):
        self.example = load_example(ROOT / "examples/support-bracket.json")

    def load_mutation(self, value):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "example.json"
            path.write_text(json.dumps(value))
            return load_example(path)

    def test_example_uses_the_complete_contract_and_expected_floor(self):
        self.assertEqual(set(self.example["expected"]["required_checks"]), REQUIRED_CHECKS)
        self.assertEqual(self.example["expected"]["floor_mm"], 2)

    def test_example_cannot_change_backend_add_code_or_weaken_checks(self):
        other_backend = {**self.example, "backend": "catia-v6"}
        arbitrary_code = {**self.example, "command": "arbitrary shell"}
        fewer_checks = deepcopy(self.example)
        fewer_checks["expected"]["required_checks"].pop()
        for value in (other_backend, arbitrary_code, fewer_checks):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.load_mutation(value)

    def test_schema_valid_numbers_must_still_obey_the_geometric_contract(self):
        value = deepcopy(self.example)
        value["parameters"]["pocket_depth_mm"] = 14
        with self.assertRaises(ValueError):
            self.load_mutation(value)
        value = deepcopy(self.example)
        value["expected"]["floor_mm"] = 3
        with self.assertRaises(ValueError):
            self.load_mutation(value)

    def test_nonfinite_values_cannot_be_serialized_as_example_inputs(self):
        value = deepcopy(self.example)
        value["parameters"]["pocket_depth_mm"] = float("nan")
        with self.assertRaises((ValueError, ValidationError)):
            self.load_mutation(value)

    def test_a_pass_label_alone_is_not_a_valid_e2e_report(self):
        with self.assertRaises(ValidationError):
            validate_document({"schema_version": "bma-cad/e2e-report/v1", "status": "passed"},
                              "e2e-report.schema.json")
