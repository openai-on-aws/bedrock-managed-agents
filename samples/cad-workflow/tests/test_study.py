from argparse import Namespace
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cad_workflow.contract import digest, expected_volume
from cad_workflow.example import ROOT
from cad_workflow.study import (
    compare, load_brief, proposals, recommendation, round_items, validate_selection,
)
from study import check_worker, environment, new_round, recover_session, verify_scope


class DesignStudyTests(unittest.TestCase):
    def setUp(self):
        self.brief = load_brief(ROOT / "examples/design-study/brief.json")
        self.review = load_brief(ROOT / "examples/design-study/review.json")

    def native(self, identifier, thickness, depth):
        parameters = {"job_id": identifier, "stock_thickness_mm": thickness, "pocket_depth_mm": depth}
        return {"parameters": parameters, "accepted": True, "checks": {"checks": [
            {"name": "analytic_volume", "measured_mm3": expected_volume(parameters)},
            {"name": "residual_floor", "measured_mm": thickness - depth},
        ]}}

    def tools(self, identifier, thickness=12, depth=10):
        return [
            {"type": "mcp_call", "name": "cad___create_support", "server_label": "cad",
             "status": "completed", "error": None,
             "arguments": json.dumps({"job_id": identifier, "stock_thickness_mm": thickness,
                                      "pocket_depth_mm": depth})},
            {"type": "mcp_call", "name": "cad___get_result", "server_label": "cad",
             "status": "completed", "error": None, "arguments": json.dumps({"job_id": identifier})},
        ]

    def test_revised_floor_rejects_an_earlier_native_accepted_design(self):
        baseline = expected_volume(self.brief["baseline"])
        native = self.native("a" * 32, 12, 10)
        initial = compare(self.brief, [native], baseline)[0]
        revised = compare(self.review, [native], baseline)[0]
        self.assertTrue(initial["native_accepted"])
        self.assertTrue(initial["meets_brief"])
        self.assertTrue(revised["native_accepted"])
        self.assertFalse(revised["meets_brief"])

    def test_3mm_floor_can_still_meet_reduction_goal(self):
        baseline = expected_volume(self.brief["baseline"])
        rows = compare(self.review, [self.native("a" * 32, 12, 9)], baseline)
        self.assertTrue(validate_selection(rows, "a" * 32))
        self.assertGreater(rows[0]["volume_reduction_percent"], 10)

    def test_recommendation_cannot_hide_infeasibility_or_claim_no_solution(self):
        rows = compare(self.review, [self.native("a" * 32, 14, 11)],
                       expected_volume(self.brief["baseline"]))
        self.assertFalse(validate_selection(rows, None))
        with self.assertRaises(ValueError):
            validate_selection(rows, "a" * 32)
        rows = compare(self.review, [self.native("b" * 32, 12, 9)],
                       expected_volume(self.brief["baseline"]))
        with self.assertRaises(ValueError):
            validate_selection(rows, None)

    def test_recommendation_must_be_lowest_volume_compliant_tested_candidate(self):
        brief = deepcopy(self.review)
        brief["requirements"]["minimum_volume_reduction_percent"] = 0
        rows = compare(brief, [self.native("a" * 32, 12, 9), self.native("b" * 32, 12, 8)],
                       expected_volume(brief["baseline"]))
        with self.assertRaises(ValueError):
            validate_selection(rows, "b" * 32)

    def test_native_failure_cannot_be_recommended(self):
        native = self.native("a" * 32, 12, 9)
        native["accepted"] = False
        rows = compare(self.review, [native], expected_volume(self.brief["baseline"]))
        self.assertIsNone(rows[0]["volume_mm3"])
        with self.assertRaises(ValueError):
            validate_selection(rows, "a" * 32)

    def test_round_reconciliation_ignores_an_older_completed_turn(self):
        items = [
            {"type": "message", "role": "assistant", "phase": "final_answer",
             "status": "completed", "turn_id": "old", "content": []},
            {"type": "message", "role": "user", "turn_id": "new",
             "content": [{"type": "input_text", "text": "CAD_STUDY_ROUND=round-two\n{}"}]},
            {"type": "mcp_call", "turn_id": "new"},
        ]
        self.assertEqual(len(round_items(items, "round-two")), 2)
        with self.assertRaises(ValueError):
            round_items(items, "missing")
        with self.assertRaises(ValueError):
            round_items(items + [items[1]], "round-two")
        with self.assertRaises(ValueError):
            round_items(items, "round-two", digest(b"changed brief"))

    def test_unallocated_job_or_missing_retrieval_fails_collection(self):
        with self.assertRaises(ValueError):
            proposals(self.tools("b" * 32), ["a" * 32])
        with self.assertRaises(ValueError):
            proposals(self.tools("a" * 32)[:1], ["a" * 32])
        self.assertEqual(set(proposals(self.tools("a" * 32), ["a" * 32])), {"a" * 32})

    def test_repeated_dimensions_or_changed_immutable_job_fails(self):
        with self.assertRaises(ValueError):
            proposals(self.tools("a" * 32) + self.tools("b" * 32), ["a" * 32, "b" * 32])
        with self.assertRaises(ValueError):
            proposals(self.tools("a" * 32) + self.tools("a" * 32, 12, 9), ["a" * 32])

    def test_review_can_reuse_verified_evidence_without_building_more_parts(self):
        known = {"a" * 32: {"parameters": self.native("a" * 32, 12, 9)["parameters"]}}
        items = self.tools("a" * 32, 12, 9)[1:]
        reused = proposals(items, [], known, minimum_new=0)
        self.assertEqual(reused, {"a" * 32: known["a" * 32]["parameters"]})
        with self.assertRaises(ValueError):
            proposals(items, ["b" * 32], known, minimum_new=2)

    def test_initial_round_requires_baseline_and_alternative_slots(self):
        for count in (0, 1):
            with self.subTest(candidate_count=count):
                brief = deepcopy(self.brief)
                brief["candidate_count"] = count
                state = {"id": "a" * 32, "rounds": [], "baseline_volume_mm3": None}
                with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
                    new_round(Path(tmp), state, brief)
                self.assertEqual(state["rounds"], [])

    def test_review_without_compute_still_checks_deployed_source(self):
        session = Mock()
        clients = {"cloudformation": Mock(), "s3": Mock()}
        session.client.side_effect = lambda name, **kwargs: clients[name]
        outputs = {"CadBackend": "freecad", "SourceSha256": "expected-source", "ArtifactBucket": "sample"}
        args = Namespace(stack="sample")
        with patch("study.stack_outputs", return_value=outputs), \
                patch("study.render", return_value={"Metadata": {"SourceSha256": "expected-source"}}), \
                patch("study.read_object", return_value=b'{"source_sha256":"expected-source"}') as bootstrap:
            self.assertEqual(check_worker(session, None, args, needs_compute=False), outputs)
            bootstrap.return_value = b'{"source_sha256":"wrong-source"}'
            with self.assertRaises(ValueError):
                check_worker(session, None, args, needs_compute=False)
            outputs["SourceSha256"] = "different-deployment"
            with self.assertRaises(ValueError):
                check_worker(session, None, args, needs_compute=False)
        self.assertNotIn("ec2", [call.args[0] for call in session.client.call_args_list])

    def test_unused_candidate_slots_do_not_force_redundant_native_jobs(self):
        tools = self.tools("a" * 32, 14, 12) + self.tools("b" * 32, 12, 10)
        self.assertEqual(len(proposals(tools, ["a" * 32, "b" * 32, "c" * 32], minimum_new=2)), 2)
        with self.assertRaises(ValueError):
            proposals(self.tools("a" * 32), ["b" * 32],
                      {"a" * 32: {"parameters": self.native("a" * 32, 12, 10)["parameters"]}},
                      minimum_new=0)

    def test_recommendation_requires_explicit_engineer_review(self):
        item = {"type": "message", "role": "assistant", "phase": "final_answer", "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps({
                    "recommended_job_id": "a" * 32, "rationale": "Measured candidate.",
                    "requires_engineer_review": False})}]}
        with self.assertRaises(ValueError):
            recommendation([item])

    def test_continuation_cannot_replace_the_baseline(self):
        state = {"id": "a" * 32, "rounds": [{"brief": self.brief}], "baseline_volume_mm3": 70172}
        changed = deepcopy(self.review)
        changed["baseline"]["stock_thickness_mm"] = 16
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            new_round(Path(tmp), state, changed)

    def test_cross_account_study_and_runtime_are_rejected(self):
        args = Namespace(account="111122223333", region="us-east-1", stack="sample")
        with self.assertRaises(ValueError):
            verify_scope({"scope": {"account": "999900001111", "region": args.region, "stack": args.stack}}, args)
        args.runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:999900001111:runtime/other"
        with self.assertRaises(ValueError):
            environment(args)

    def test_infeasible_brief_is_valid_input_but_invalid_numbers_are_not(self):
        value = deepcopy(self.review)
        value["requirements"]["minimum_floor_mm"] = 19
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brief.json"
            path.write_text(json.dumps(value))
            self.assertEqual(load_brief(path)["requirements"]["minimum_floor_mm"], 19)
            value["requirements"]["minimum_floor_mm"] = float("nan")
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                load_brief(path)

    def recovery_fixture(self):
        text = "CAD_STUDY_ROUND=round-one\n{}"
        state = {"id": "a" * 32, "session_id": None, "created_at": "2026-10-19T23:00:00+00:00",
                 "session_role_arn": "synthetic-role", "environment": {"type": "none"},
                 "outputs": {"ModelId": "synthetic-model"},
                 "rounds": [{"id": "round-one", "input_sha256": digest(text.encode()),
                             "status": "submission_unknown", "collected": False}]}
        remote = {"id": "opaque-service-session", "created_at": 1792450860,
                  "role_arn": "synthetic-role", "agent": {"model": "synthetic-model"},
                  "environment": {"type": "none"}}
        item = {"type": "message", "role": "user", "turn_id": "opaque-turn",
                "content": [{"type": "input_text", "text": text}]}
        client = Mock()
        client.beta.agents.sessions.list.return_value = [Mock(model_dump=Mock(return_value=remote))]
        client.beta.agents.sessions.items.list.return_value = [Mock(model_dump=Mock(return_value=item))]
        return state, remote, item, client

    def test_create_timeout_recovers_exact_durable_input_without_another_submission(self):
        state, _, _, client = self.recovery_fixture()
        with tempfile.TemporaryDirectory() as tmp, patch("study.output"):
            recover_session(client, Path(tmp), state)
        self.assertEqual(state["session_id"], "opaque-service-session")
        client.beta.agents.sessions.create.assert_not_called()
        client.beta.agents.sessions.events.create.assert_not_called()

    def test_missing_or_ambiguous_recovery_never_creates_another_session(self):
        for mode in ("missing", "duplicate"):
            state, remote, _, client = self.recovery_fixture()
            if mode == "missing":
                state["rounds"][0]["input_sha256"] = digest(b"not this study")
            else:
                client.beta.agents.sessions.list.return_value *= 2
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
                recover_session(client, Path(tmp), state)
            self.assertIsNone(state["session_id"])
            client.beta.agents.sessions.create.assert_not_called()
