"""Tests for S2, labeler agreement, trace metrics, and the pilot comparison."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation import compare_pilot  # noqa: E402
from evaluation.agreement import cohens_kappa, labeler_agreement  # noqa: E402
from evaluation.metrics import TRACE_ONLY_ERRORS, CaseFacts, score_system  # noqa: E402
from evaluation.pilot_data import Label, load_dataset  # noqa: E402
from evaluation.systems import (  # noqa: E402
    ESCALATE_ALL,
    ESCALATE_ALL_REASON,
    Prediction,
    predict_escalate_all,
    predict_scripted_agent,
)
from evaluation.trace_metrics import ClaimService, measure_traces, trace_safety_counts  # noqa: E402

AS_OF = date(2026, 9, 6)
SET_A_MANIFEST = REPO_ROOT / "datasets" / "development" / "set_a" / "manifest.json"
SET_B_MANIFEST = REPO_ROOT / "datasets" / "development" / "set_b" / "manifest.json"


def _cases(manifest):
    return {case.case_id: case for case in load_dataset(manifest).cases}


class EscalateAllTest(unittest.TestCase):
    """S2 escalates every agent-routed case and otherwise matches S0."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {**_cases(SET_A_MANIFEST), **_cases(SET_B_MANIFEST)}

    def test_agent_routed_case_goes_to_manual_review(self):
        case = self.cases["CLM-2026-005097-L1"]
        prediction = predict_escalate_all(case, AS_OF)

        self.assertEqual(prediction.system, ESCALATE_ALL)
        self.assertEqual(prediction.disposition, "MANUAL_REVIEW")
        self.assertEqual(prediction.root_cause, "MEDICAL_NECESSITY")
        self.assertIsNone(prediction.deadline)
        self.assertEqual(prediction.approval_state, "PENDING_REVIEW")
        self.assertEqual(prediction.details["route_outcome"], "AGENT_REQUIRED")
        self.assertIn(ESCALATE_ALL_REASON, prediction.details["manual_review_reasons"])
        self.assertEqual(prediction.trace_events, ())

    def test_matches_s0_on_every_case_the_agent_does_not_handle(self):
        for case_id, case in sorted(self.cases.items()):
            s2 = predict_escalate_all(case, AS_OF)
            if s2.details["route_outcome"] == "AGENT_REQUIRED":
                continue
            with self.subTest(case=case_id):
                s0 = predict_scripted_agent(case, AS_OF)
                self.assertEqual(
                    (s2.disposition, s2.root_cause, s2.deadline),
                    (s0.disposition, s0.root_cause, s0.deadline),
                )


class TraceOnlyErrorsTest(unittest.TestCase):
    def test_trace_only_errors_are_unmeasured_even_with_traces(self):
        labels = {"A-L1": Label("A-L1", "CODING", "WRITE_OFF", False, (), None, ())}
        predictions = {"A-L1": Prediction("A-L1", "s", "WRITE_OFF", "CODING", None, "PENDING_REVIEW")}
        facts = {"A-L1": CaseFacts("CO", 180, 365, date(2026, 7, 1), date(2026, 6, 1), False)}

        score = score_system("s", predictions, labels, facts, AS_OF, has_traces=True)

        for error in TRACE_ONLY_ERRORS:
            self.assertIn(error, score.safety_unmeasured)


class AgreementTest(unittest.TestCase):
    def test_kappa_matches_hand_calculation(self):
        # Observed 3/4; each rater uses x and y twice, so chance is 1/2.
        pairs = [("x", "x"), ("y", "y"), ("x", "x"), ("y", "x")]
        observed, expected = 0.75, (3 * 2 + 1 * 2) / 16
        self.assertAlmostEqual(cohens_kappa(pairs), (observed - expected) / (1 - expected))

    def test_kappa_is_undefined_for_one_shared_value(self):
        self.assertIsNone(cohens_kappa([("x", "x"), ("x", "x")]))

    def test_label_files_compare_field_by_field(self):
        first = {"cases": [
            {"case_id": "C1", "disposition": "APPEAL_WITH_DOCUMENTATION", "root_cause": "MEDICAL_NECESSITY",
             "manual_review_reasons": [], "criteria": [{"criterion_id": "A", "status": "SUPPORTED"}]},
            {"case_id": "C2", "disposition": "WRITE_OFF", "root_cause": "MEDICAL_NECESSITY",
             "manual_review_reasons": [], "criteria": []},
        ]}
        second = json.loads(json.dumps(first))
        second["cases"][1]["disposition"] = "MANUAL_REVIEW"
        second["cases"][0]["criteria"][0]["status"] = "UNKNOWN"

        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a.json", Path(directory) / "b.json"
            a.write_text(json.dumps(first), encoding="utf-8")
            b.write_text(json.dumps(second), encoding="utf-8")
            result = {item.field: item for item in labeler_agreement(a, b)}

        self.assertEqual((result["disposition"].agreed, result["disposition"].total), (1, 2))
        self.assertEqual((result["root_cause"].agreed, result["root_cause"].total), (2, 2))
        self.assertEqual((result["criterion_status"].agreed, result["criterion_status"].total), (0, 1))


def _event(kind, payload=None, **extra):
    return {"event_type": kind, "payload": payload or {}, **extra}


START = _event("RUN_STARTED", {"config": {"max_model_calls": 4, "max_tool_calls": 3,
                                          "max_repair_attempts": 1, "max_total_steps": 7}})
SERVICE = ClaimService("BCBS_AZ", "70553", "2026-06-02")


class TraceMetricsTest(unittest.TestCase):
    def test_clean_run_has_no_trace_errors(self):
        run = [
            START,
            _event("TOOL_REQUESTED", {"tool_name": "retrieve_medical_policy", "tool_args": {
                "payer": "BCBS_AZ", "procedure_code": "70553", "service_date": "2026-06-02"}}),
            _event("TOOL_RETURNED", {"status": "OK", "error_code": None, "passage_ids": ["P1", "N1"]}),
            _event("DECISION_PROPOSED", {"citation_ids": ["P1", "N1"]}),
            _event("VALIDATION_RETURNED", {"outcome": "ACCEPTED"}),
            _event("RUN_COMPLETED", {"counters": {"model_calls": 2, "tool_calls": 1, "total_steps": 3}}),
        ]
        metrics = measure_traces({"C1": run}, {"C1": SERVICE})

        self.assertEqual((metrics.citations_retrieved, metrics.citations), (2, 2))
        self.assertEqual(metrics.decisions_accepted, 1)
        self.assertEqual(trace_safety_counts(metrics), {
            "CSE_FABRICATED_CITATION": 0, "CSE_WRONG_POLICY": 0, "CSE_DEADLINE_LOST_TO_FAILURE": 0})

    def test_trace_errors_are_counted(self):
        run = [
            START,
            _event("TOOL_REQUESTED", {"tool_name": "retrieve_medical_policy", "tool_args": {
                "payer": "AETNA", "procedure_code": "70553", "service_date": "2026-06-02"}}),
            _event("TOOL_RETURNED", {"status": "OK", "error_code": None, "passage_ids": ["P1"]}),
            _event("DECISION_PROPOSED", {"citation_ids": ["P1", "NOT-RETRIEVED"]}),
        ]
        over_budget = [
            START,
            _event("MANUAL_REVIEW", {"counters": {"total_steps": 9}}, terminal_reason="BUDGET_EXHAUSTED"),
        ]
        metrics = measure_traces({"C1": run, "C2": over_budget}, {"C1": SERVICE, "C2": SERVICE})

        self.assertEqual(trace_safety_counts(metrics), {
            "CSE_FABRICATED_CITATION": 1, "CSE_WRONG_POLICY": 1, "CSE_DEADLINE_LOST_TO_FAILURE": 1})
        self.assertEqual(metrics.unretrieved_citations, [("C1", "NOT-RETRIEVED")])
        self.assertEqual(metrics.budget_overruns, 1)
        self.assertEqual(metrics.other_terminations, 2)


class ComparePilotTest(unittest.TestCase):
    def test_default_output_is_local_and_not_committed_evidence(self):
        self.assertEqual(
            compare_pilot.DEFAULT_OUTDIR,
            REPO_ROOT / ".local-output" / "eval" / "m2-pilot",
        )

    def test_nonempty_output_directory_is_rejected_before_a_run(self):
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory)
            existing_trace = output_directory / "existing-trace.jsonl"
            existing_trace.write_text("preserve me\n", encoding="utf-8")

            with self.assertRaisesRegex(
                FileExistsError,
                "Choose a fresh --outdir",
            ):
                compare_pilot.run(["--outdir", str(output_directory)])

            self.assertEqual(
                existing_trace.read_text(encoding="utf-8"),
                "preserve me\n",
            )

    def test_comparison_covers_both_datasets_and_all_systems(self):
        with tempfile.TemporaryDirectory() as directory:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(compare_pilot.run(["--outdir", directory]), 0)
            out = Path(directory)
            summary = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
            report = (out / "comparison.md").read_text(encoding="utf-8")

        self.assertEqual(set(summary), {"A", "B"})
        self.assertEqual(summary["A"]["cases"], 23)
        self.assertEqual(summary["B"]["cases"], 12)
        for dataset in summary.values():
            self.assertEqual(set(dataset["systems"]), {"scripted-agent", "baseline-v1", "escalate-all"})
        # S2 escalates every agent-routed Dataset B case, so it resolves none of them.
        self.assertEqual(summary["B"]["systems"]["escalate-all"]["rates"]["coverage"]["hits"], 0)
        self.assertEqual(summary["B"]["trace_safety_counts"]["CSE_FABRICATED_CITATION"], 0)
        self.assertIn("## Dataset A", report)
        self.assertIn("## Dataset B", report)


if __name__ == "__main__":
    unittest.main()
