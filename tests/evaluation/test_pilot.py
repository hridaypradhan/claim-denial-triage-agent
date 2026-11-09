"""Tests for the pilot evaluation harness (issue #12)."""

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

from evaluation.metrics import CaseFacts, Rate, safety_errors, score_system  # noqa: E402
from evaluation.pilot_data import DatasetError, Label, load_dataset, load_labels  # noqa: E402
from evaluation.run_pilot import case_facts, run  # noqa: E402
from evaluation.systems import Prediction, predict_baseline_v1  # noqa: E402

SET_A_MANIFEST = REPO_ROOT / "datasets" / "development" / "set_a" / "manifest.json"
SET_A_LABELS = REPO_ROOT / "datasets" / "labels" / "set_a_development.labeler_a.json"
AS_OF = date(2026, 9, 6)


def label(disposition, root="CODING", reasons=(), deadline=None):
    return Label("C-L1", root, disposition, disposition == "MANUAL_REVIEW", tuple(reasons), deadline, ())


def prediction(disposition, root="CODING", deadline=None, approval=None):
    return Prediction("C-L1", "test", disposition, root, deadline, approval)


FACTS_CO = CaseFacts("CO", 180, 365, date(2026, 7, 1), date(2026, 6, 1), abn_signed=False)


class RateTest(unittest.TestCase):
    def test_empty_rate_is_not_zero(self):
        self.assertIsNone(Rate(0, 0).value)
        self.assertIsNone(Rate(0, 0).wilson())
        self.assertEqual(Rate(0, 0).text(), "n/a (no cases)")

    def test_wilson_interval_contains_estimate(self):
        low, high = Rate(9, 12).wilson()
        self.assertLess(low, 0.75)
        self.assertGreater(high, 0.75)
        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(high, 1.0)


class SafetyErrorTest(unittest.TestCase):
    def test_contractual_appeal(self):
        errors = safety_errors(prediction("APPEAL_WITH_DOCUMENTATION", deadline="2026-12-28"),
                               label("NO_ACTION_CONTRACTUAL", root="CONTRACTUAL"), FACTS_CO, AS_OF)
        self.assertIn("CSE_CONTRACTUAL_APPEAL", errors)
        self.assertNotIn("CSE_UNSUPPORTED_APPEAL", errors)

    def test_unsupported_appeal(self):
        errors = safety_errors(prediction("APPEAL_WITH_DOCUMENTATION", deadline="2026-12-28"),
                               label("MANUAL_REVIEW", reasons=["POLICY_NOT_APPLICABLE"]), FACTS_CO, AS_OF)
        self.assertEqual(errors, ["CSE_UNSUPPORTED_APPEAL"])

    def test_patient_billing_on_co_without_abn(self):
        errors = safety_errors(prediction("BILL_PATIENT"), label("WRITE_OFF"), FACTS_CO, AS_OF)
        self.assertEqual(errors, ["CSE_UNJUSTIFIED_PATIENT_BILLING"])

    def test_patient_billing_on_pr_is_allowed(self):
        facts = CaseFacts("PR", 180, 365, date(2026, 7, 1), date(2026, 6, 1), abn_signed=False)
        self.assertEqual(safety_errors(prediction("BILL_PATIENT"), label("BILL_PATIENT"), facts, AS_OF), [])

    def test_missed_escalation(self):
        errors = safety_errors(prediction("WRITE_OFF"), label("MANUAL_REVIEW", reasons=["MISSING_EVIDENCE"]),
                               FACTS_CO, AS_OF)
        self.assertEqual(errors, ["CSE_MISSED_ESCALATION"])

    def test_unnecessary_manual_review_is_not_a_safety_error(self):
        self.assertEqual(safety_errors(prediction("MANUAL_REVIEW"), label("WRITE_OFF"), FACTS_CO, AS_OF), [])

    def test_wrong_deadline(self):
        errors = safety_errors(prediction("CORRECT_AND_RESUBMIT", deadline="2027-06-02"),
                               label("CORRECT_AND_RESUBMIT", deadline="2027-06-01"), FACTS_CO, AS_OF)
        self.assertEqual(errors, ["CSE_WRONG_DEADLINE"])

    def test_action_after_deadline(self):
        facts = CaseFacts("CO", 180, 180, date(2026, 2, 18), date(2026, 1, 20), abn_signed=False)
        errors = safety_errors(prediction("CORRECT_AND_RESUBMIT", deadline="2026-07-19"),
                               label("MANUAL_REVIEW", reasons=["DEADLINE_EXPIRED"]), facts, AS_OF)
        self.assertEqual(errors, ["CSE_ACTION_AFTER_DEADLINE"])

    def test_approval_bypass(self):
        errors = safety_errors(prediction("WRITE_OFF", approval="APPROVED"), label("WRITE_OFF"), FACTS_CO, AS_OF)
        self.assertEqual(errors, ["CSE_APPROVAL_BYPASS"])


class ScoreSystemTest(unittest.TestCase):
    def test_coverage_selective_and_unmeasured(self):
        labels = {"A-L1": Label("A-L1", "CODING", "WRITE_OFF", False, (), None, ()),
                  "B-L1": Label("B-L1", "CODING", "MANUAL_REVIEW", True, ("MISSING_EVIDENCE",), None, ())}
        preds = {"A-L1": Prediction("A-L1", "s", "MANUAL_REVIEW", "CODING", None),
                 "B-L1": Prediction("B-L1", "s", "MANUAL_REVIEW", "CODING", None)}
        facts = {cid: FACTS_CO for cid in labels}
        score = score_system("s", preds, labels, facts, AS_OF, has_traces=False)
        self.assertEqual((score.joint.hits, score.joint.total), (1, 2))
        self.assertEqual((score.coverage.hits, score.coverage.total), (0, 1))
        self.assertEqual((score.selective.hits, score.selective.total), (0, 0))
        self.assertEqual((score.unnecessary_manual_review.hits, score.unnecessary_manual_review.total), (1, 1))
        self.assertIn("CSE_FABRICATED_CITATION", score.safety_unmeasured)
        self.assertIn("CSE_APPROVAL_BYPASS", score.safety_unmeasured)


class SetADatasetTest(unittest.TestCase):
    def test_manifest_and_labels_are_consistent(self):
        dataset = load_dataset(SET_A_MANIFEST)
        labels = load_labels(SET_A_LABELS, dataset)
        self.assertEqual(len(dataset.cases), 23)
        self.assertEqual(set(labels), {c.case_id for c in dataset.cases})
        families = {}
        for case in dataset.cases:
            families.setdefault(case.scenario_family_id, set()).add(case.case_id)
        self.assertTrue(all(families.values()))

    def test_missing_label_is_rejected(self):
        dataset = load_dataset(SET_A_MANIFEST)
        raw = json.loads(SET_A_LABELS.read_text(encoding="utf-8"))
        raw["cases"] = raw["cases"][1:]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(DatasetError):
                load_labels(path, dataset)

    def _load_with(self, change):
        """Load Set A labels after applying ``change`` to the raw JSON."""

        dataset = load_dataset(SET_A_MANIFEST)
        raw = json.loads(SET_A_LABELS.read_text(encoding="utf-8"))
        change(raw)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            return load_labels(path, dataset)

    def test_manual_review_flag_must_be_a_json_boolean(self):
        def change(raw):
            raw["cases"][0]["requires_manual_review"] = "false"
        with self.assertRaisesRegex(DatasetError, "true/false"):
            self._load_with(change)

    def test_label_identity_must_match_its_case(self):
        mismatches = {
            "dataset": "B",
            "split": "held_out",
            "as_of": "2026-01-01",
            "claim_id": "CLM-0000-000000",
            "service_line_id": 2,
            "scenario_family_id": "WRONG-FAMILY",
        }
        for field, value in mismatches.items():
            with self.subTest(field=field):
                def change(raw, field=field, value=value):
                    raw["cases"][0][field] = value
                with self.assertRaises(DatasetError):
                    self._load_with(change)

    def test_service_line_id_must_be_an_integer(self):
        def change(raw):
            raw["cases"][0]["service_line_id"] = str(raw["cases"][0]["service_line_id"])
        with self.assertRaisesRegex(DatasetError, "integer"):
            self._load_with(change)

    def test_label_file_dataset_must_match_manifest(self):
        def change(raw):
            raw["dataset"] = "B"
        with self.assertRaises(DatasetError):
            self._load_with(change)

    def test_every_case_gets_one_baseline_prediction(self):
        dataset = load_dataset(SET_A_MANIFEST)
        for case in dataset.cases:
            with self.subTest(case=case.case_id):
                pred = predict_baseline_v1(case, AS_OF)
                self.assertEqual(pred.case_id, case.case_id)
        self.assertEqual(len(case_facts(dataset)), 23)

    def test_end_to_end_run_writes_outputs(self):
        # The report is intentionally printed by the real CLI. Capture it in
        # this artifact-focused test so quiet test runs remain easy to scan.
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            code = run(["--manifest", str(SET_A_MANIFEST), "--labels", str(SET_A_LABELS),
                        "--experiment", "unit-test", "--outdir", tmp])
            self.assertEqual(code, 0)
            out = Path(tmp) / "unit-test"
            for name in ("predictions.jsonl", "scores.json", "results.md", "run_manifest.json"):
                self.assertTrue((out / name).is_file(), name)
            self.assertEqual(len((out / "predictions.jsonl").read_text(encoding="utf-8").splitlines()), 23)

    def test_scripted_agent_run_preserves_packages_and_traces(self):
        """S0 should preserve every result and each executed runtime trace."""

        with (
            tempfile.TemporaryDirectory() as tmp,
            redirect_stdout(io.StringIO()),
        ):
            code = run(
                [
                    "--manifest",
                    str(SET_A_MANIFEST),
                    "--labels",
                    str(SET_A_LABELS),
                    "--experiment",
                    "set-a-scripted-agent",
                    "--system",
                    "scripted-agent",
                    "--outdir",
                    tmp,
                ]
            )

            self.assertEqual(code, 0)

            output_directory = Path(tmp) / "set-a-scripted-agent"
            output_packages = list(
                (output_directory / "output_packages").glob("*.json")
            )
            traces = list(
                (output_directory / "traces").glob("*.jsonl")
            )

            self.assertEqual(len(output_packages), 23)
            self.assertEqual(len(traces), 2)

            predictions = [
                json.loads(line)
                for line in (
                    output_directory / "predictions.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(predictions), 23)
            self.assertEqual(
                {prediction["system"] for prediction in predictions},
                {"scripted-agent"},
            )

            run_manifest = json.loads(
                (output_directory / "run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                run_manifest["output_package_count"],
                23,
            )
            self.assertEqual(
                run_manifest["canonical_trace_count"],
                2,
            )

            scores = json.loads(
                (output_directory / "scores.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                scores["rates"]["joint"],
                {"hits": 22, "total": 23},
            )
            unknown_payer_result = next(
                result
                for result in scores["per_case"]
                if result["case_id"] == "CLM-2026-610116-L1"
            )
            self.assertEqual(
                unknown_payer_result["predicted_disposition"],
                "MANUAL_REVIEW",
            )
            self.assertEqual(
                unknown_payer_result["safety_errors"],
                [],
            )
            self.assertEqual(
                scores["safety_counts"].get(
                    "CSE_UNSUPPORTED_APPEAL",
                    0,
                ),
                0,
            )

    def test_run_prints_on_a_windows_console(self):
        """A cp1252 console (default Windows) must not crash the run."""

        from unittest import mock

        raw = io.BytesIO()
        console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, "stdout", console):
            code = run(["--manifest", str(SET_A_MANIFEST), "--labels", str(SET_A_LABELS),
                        "--experiment", "cp1252", "--outdir", tmp])
            console.flush()
            report = (Path(tmp) / "cp1252" / "results.md").read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertTrue(report.isascii(), "results.md should use ASCII punctuation")


if __name__ == "__main__":
    unittest.main()
