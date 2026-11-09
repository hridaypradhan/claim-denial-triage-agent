"""Tests for the escalate-all baseline, labeler agreement, and the comparison run."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.agreement import cohen_kappa, compare_label_files  # noqa: E402
from evaluation.compare_systems import DATASETS, SYSTEMS, main  # noqa: E402
from evaluation.pilot_data import load_dataset  # noqa: E402
from evaluation.systems import predict_baseline_v1, predict_escalate_all  # noqa: E402

AS_OF = date(2026, 9, 6)


class CohenKappaTest(unittest.TestCase):
    def test_textbook_value(self):
        # observed 0.75, chance 0.5 -> kappa 0.5
        self.assertAlmostEqual(cohen_kappa(list("yynn"), list("ynnn")), 0.5)

    def test_perfect_agreement_is_one(self):
        self.assertAlmostEqual(cohen_kappa(list("abca"), list("abca")), 1.0)

    def test_single_category_is_undefined(self):
        self.assertIsNone(cohen_kappa(["x", "x"], ["x", "x"]))

    def test_unequal_lengths_are_rejected(self):
        with self.assertRaises(ValueError):
            cohen_kappa(["a"], ["a", "b"])


class AgreementFileTest(unittest.TestCase):
    def test_disagreements_are_listed(self):
        first, second = DATASETS["A"]["labelers"]
        result = compare_label_files(first, second)
        self.assertEqual(result["cases"], 23)
        listed = {d["case_id"] for d in result["disagreements"]}
        joint = next(f for f in result["fields"] if f.field == "joint")
        self.assertEqual(len(listed), joint.items - joint.agreed)

    def test_criterion_status_is_compared_for_dataset_b(self):
        result = compare_label_files(*DATASETS["B"]["labelers"])
        self.assertIn("criterion_status", [f.field for f in result["fields"]])

    def test_files_with_different_cases_are_rejected(self):
        first, _ = DATASETS["A"]["labelers"]
        raw = json.loads(first.read_text(encoding="utf-8-sig"))
        raw["cases"] = raw["cases"][1:]
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "labels.json"
            other.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ValueError):
                compare_label_files(first, other)


class EscalateAllTest(unittest.TestCase):
    def test_every_case_goes_to_manual_review_with_baseline_root_cause(self):
        for name in ("A", "B"):
            for case in load_dataset(DATASETS[name]["manifest"]).cases:
                with self.subTest(case=case.case_id):
                    prediction = predict_escalate_all(case, AS_OF)
                    self.assertEqual(prediction.disposition, "MANUAL_REVIEW")
                    self.assertIsNone(prediction.deadline)
                    self.assertEqual(prediction.root_cause, predict_baseline_v1(case, AS_OF).root_cause)


class CompareSystemsTest(unittest.TestCase):
    def test_runs_every_system_on_both_datasets(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--experiment", "unit", "--outdir", tmp]), 0)
            out = Path(tmp) / "unit"
            report = (out / "comparison.md").read_text(encoding="utf-8")
            data = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
        for name in ("A", "B"):
            self.assertEqual(set(data["scores"][name]), {system for _, system in SYSTEMS})
            self.assertIn("## Dataset %s" % name, report)
        self.assertIn("Cohen's kappa", report)
        self.assertNotIn("Rate(", report)
        self.assertTrue(report.isascii())


if __name__ == "__main__":
    unittest.main()
