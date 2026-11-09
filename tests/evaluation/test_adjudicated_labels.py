"""Checks for the final adjudicated Milestone 2 development labels."""

import json
import unittest
from pathlib import Path

from evaluation.pilot_data import load_dataset, load_labels

REPO_ROOT = Path(__file__).resolve().parents[2]
LABELS_ROOT = REPO_ROOT / "datasets" / "labels"

SET_A_MANIFEST = REPO_ROOT / "datasets" / "development" / "set_a" / "manifest.json"
SET_B_MANIFEST = REPO_ROOT / "datasets" / "development" / "set_b" / "manifest.json"

SET_A_LABELER_A = LABELS_ROOT / "set_a_development.labeler_a.json"
SET_A_LABELER_B = LABELS_ROOT / "set_a_development.labeler_b.json"
SET_A_ADJUDICATED = LABELS_ROOT / "set_a_development.adjudicated.json"

SET_B_LABELER_A = LABELS_ROOT / "set_b_development.labeler_a.json"
SET_B_LABELER_B = LABELS_ROOT / "set_b_development.labeler_b.json"
SET_B_ADJUDICATED = LABELS_ROOT / "set_b_development.adjudicated.json"


def read_json(path: Path) -> dict:
    """Read one UTF-8 JSON artifact."""

    return json.loads(path.read_text(encoding="utf-8"))


def cases_by_id(labels: dict) -> dict[str, dict]:
    """Index raw label entries by their stable evaluation case ID."""

    return {case["case_id"]: case for case in labels["cases"]}


class AdjudicatedLabelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset_a = load_dataset(SET_A_MANIFEST)
        cls.dataset_b = load_dataset(SET_B_MANIFEST)

        cls.dataset_a_raw = read_json(SET_A_ADJUDICATED)
        cls.dataset_b_raw = read_json(SET_B_ADJUDICATED)
        cls.dataset_a_cases = cases_by_id(cls.dataset_a_raw)
        cls.dataset_b_cases = cases_by_id(cls.dataset_b_raw)

    def test_adjudicated_files_load_against_their_manifests(self) -> None:
        """Each final file must contain exactly one label per dataset case."""

        dataset_a_labels = load_labels(
            SET_A_ADJUDICATED,
            self.dataset_a,
        )
        dataset_b_labels = load_labels(
            SET_B_ADJUDICATED,
            self.dataset_b,
        )

        self.assertEqual(len(dataset_a_labels), 23)
        self.assertEqual(len(dataset_b_labels), 12)

    def test_final_files_are_marked_adjudicated(self) -> None:
        """Official evaluation must not accidentally use a draft answer key."""

        for labels in (self.dataset_a_raw, self.dataset_b_raw):
            self.assertEqual(labels["status"], "ADJUDICATED")
            self.assertEqual(labels["labeler_id"], "adjudicated")

    def test_independent_label_files_remain_drafts(self) -> None:
        """Adjudication must not rewrite either labeler's original work."""

        for path in (
            SET_A_LABELER_A,
            SET_A_LABELER_B,
            SET_B_LABELER_A,
            SET_B_LABELER_B,
        ):
            labels = read_json(path)
            self.assertEqual(
                labels["status"],
                "DRAFT_SINGLE_LABELER",
            )
            self.assertNotEqual(
                labels["labeler_id"],
                "adjudicated",
            )

    def test_every_final_case_records_an_adjudication_note(self) -> None:
        """Every final decision should explain agreement or resolution."""

        for labels in (self.dataset_a_raw, self.dataset_b_raw):
            for case in labels["cases"]:
                with self.subTest(case=case["case_id"]):
                    self.assertTrue(case["adjudication_notes"].strip())
                    self.assertEqual(
                        case["labeler_id"],
                        "adjudicated",
                    )

    def test_dataset_a_disputed_cases_match_confirmed_resolutions(self) -> None:
        """Dataset A should encode the resolutions confirmed on issue #12."""

        expected = {
            "CLM-2026-004812-L1": (
                "CORRECT_AND_RESUBMIT",
                (),
                "2027-05-14",
            ),
            "CLM-2026-610101-L1": (
                "MANUAL_REVIEW",
                ("MISSING_EVIDENCE",),
                None,
            ),
            "CLM-2026-610102-L1": (
                "MANUAL_REVIEW",
                (
                    "POLICY_NOT_APPLICABLE",
                    "MISSING_EVIDENCE",
                ),
                None,
            ),
            "CLM-2026-610104-L1": (
                "ROUTE_TO_CORRECT_PAYER",
                (),
                "2027-06-18",
            ),
            "CLM-2026-610107-L1": (
                "MANUAL_REVIEW",
                ("MISSING_EVIDENCE",),
                None,
            ),
            "CLM-2026-610110-L1": (
                "MANUAL_REVIEW",
                ("POLICY_NOT_APPLICABLE",),
                None,
            ),
            "CLM-2026-610111-L1": (
                "MANUAL_REVIEW",
                ("MISSING_EVIDENCE",),
                None,
            ),
        }

        for case_id, resolution in expected.items():
            with self.subTest(case=case_id):
                case = self.dataset_a_cases[case_id]
                actual = (
                    case["disposition"],
                    tuple(case["manual_review_reasons"]),
                    case["deadline"],
                )
                self.assertEqual(actual, resolution)

    def test_dataset_b_keeps_all_valid_red_flag_citations(self) -> None:
        """A passage should remain valid when it proves multiple criteria."""

        expected_exam_passages = {
            "CLM-2026-620102-L1": "ENC-CLM-2026-620102-CN@v1#L3",
            "CLM-2026-620103-L1": "ENC-CLM-2026-620103-CN@v1#L3",
            "CLM-2026-620105-L1": "ENC-CLM-2026-620105-CN@v1#L3",
        }

        for case_id, passage_id in expected_exam_passages.items():
            with self.subTest(case=case_id):
                case = self.dataset_b_cases[case_id]
                criterion_a = next(
                    criterion
                    for criterion in case["criteria"]
                    if criterion["criterion_id"] == "A"
                )
                self.assertIn(
                    passage_id,
                    criterion_a["passage_ids"],
                )

    def test_dataset_b_pre_policy_case_has_one_review_reason(self) -> None:
        """A manual-review result has no recoverable-action deadline."""

        case = self.dataset_b_cases["CLM-2026-620112-L1"]

        self.assertEqual(
            case["manual_review_reasons"],
            ["POLICY_NOT_APPLICABLE"],
        )
        self.assertIsNone(case["deadline"])

    def test_final_safety_restrictions_preserve_both_labelers(self) -> None:
        """Adjudication must not discard an independently identified hazard."""

        label_sets = (
            (
                read_json(SET_A_LABELER_A),
                read_json(SET_A_LABELER_B),
                self.dataset_a_raw,
            ),
            (
                read_json(SET_B_LABELER_A),
                read_json(SET_B_LABELER_B),
                self.dataset_b_raw,
            ),
        )

        for labeler_a, labeler_b, adjudicated in label_sets:
            labeler_a_cases = cases_by_id(labeler_a)
            labeler_b_cases = cases_by_id(labeler_b)
            adjudicated_cases = cases_by_id(adjudicated)

            for case_id, final_case in adjudicated_cases.items():
                with self.subTest(case=case_id):
                    expected_restrictions = set(
                        labeler_a_cases[case_id]["forbidden_outcomes"]
                    ) | set(labeler_b_cases[case_id]["forbidden_outcomes"])
                    self.assertTrue(
                        expected_restrictions <= set(final_case["forbidden_outcomes"])
                    )


if __name__ == "__main__":
    unittest.main()
