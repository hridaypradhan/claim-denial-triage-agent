"""Consistency checks for Dataset B (evidence-dependent policy review).

These check the draft labels against the knowledge-v1 corpus and rubric v0.1
section 3 step 4. They do not decide whether a label is right; that is the
job of the second labeler and adjudication.
"""

import json
import re
import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.pilot_data import load_dataset, load_labels  # noqa: E402

SET_B_MANIFEST = REPO_ROOT / "datasets" / "development" / "set_b" / "manifest.json"
SET_B_LABELS = REPO_ROOT / "datasets" / "labels" / "set_b_development.labeler_a.json"
SET_A_MANIFEST = REPO_ROOT / "datasets" / "development" / "set_a" / "manifest.json"
KNOWLEDGE = REPO_ROOT / "knowledge"
AS_OF = date(2026, 9, 6)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _knowledge():
    passages, policies = {}, {}
    for path in KNOWLEDGE.rglob("*.v*.json"):
        doc = _read(path)
        for passage in doc.get("passages", []):
            passages[passage["passage_id"]] = doc
        if doc.get("source_type") == "medical_policy":
            policies["%s@v%s" % (doc["source_id"], doc["version"])] = doc
    return passages, policies


def _applicable_policy(policies, claim):
    """The one policy for this payer, procedure, and service date, or None."""

    cpt = claim["service_lines"][0]["cpt"]
    dos = claim["date_of_service"]
    matches = [
        key for key, doc in policies.items()
        if doc["payer"] == claim["payer"]
        and cpt in doc["procedure_codes"]
        and doc["effective_from"] <= dos
        and (doc["effective_to"] is None or dos <= doc["effective_to"])
    ]
    return matches[0] if len(matches) == 1 else None


def _rubric_disposition(formula, statuses):
    """Rubric section 3 step 4, rules 1-3, for a CO balance with no ABN."""

    expression = re.sub(r"\b([A-Z])\b", lambda m: str(statuses[m.group(1)] == "SUPPORTED"), formula)
    if eval(expression.replace("AND", "and").replace("OR", "or"), {"__builtins__": {}}):
        return "APPEAL_WITH_DOCUMENTATION"
    if any(status in ("UNKNOWN", "CONFLICTING") for status in statuses.values()):
        return "MANUAL_REVIEW"
    return "WRITE_OFF"


class SetBDatasetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(SET_B_MANIFEST)
        cls.labels = load_labels(SET_B_LABELS, cls.dataset)
        cls.raw_labels = {entry["case_id"]: entry for entry in _read(SET_B_LABELS)["cases"]}
        cls.claims = {case.case_id: _read(case.claim_path) for case in cls.dataset.cases}
        cls.passages, cls.policies = _knowledge()

    def test_twelve_cases_one_per_planned_claim(self):
        expected = {"CLM-2026-005097-L1"} | {"CLM-2026-6201%02d-L1" % n for n in range(2, 13)}
        self.assertEqual({case.case_id for case in self.dataset.cases}, expected)

    def test_no_case_is_also_in_dataset_a(self):
        set_a = {case.case_id for case in load_dataset(SET_A_MANIFEST).cases}
        self.assertFalse(set_a & set(self.labels))

    def test_policy_is_the_one_that_applies(self):
        for case_id, claim in self.claims.items():
            with self.subTest(case=case_id):
                self.assertEqual(self.raw_labels[case_id]["policy_source_id"],
                                 _applicable_policy(self.policies, claim))

    def test_every_cited_passage_resolves_to_this_claim_before_service(self):
        for case_id, entry in self.raw_labels.items():
            claim = self.claims[case_id]
            cited = list(entry["supporting_passage_ids"])
            for criterion in entry["criteria"]:
                cited += criterion["passage_ids"]
                self.assertTrue(set(criterion["passage_ids"]) <= set(entry["supporting_passage_ids"]))
            for passage_id in cited:
                with self.subTest(case=case_id, passage=passage_id):
                    self.assertIn(passage_id, self.passages)
                    source = self.passages[passage_id]
                    self.assertIn(source.get("claim_id"), (None, claim["claim_id"]))
                    if source.get("note_date"):
                        self.assertLessEqual(source["note_date"], claim["date_of_service"])

    def test_labels_follow_rubric_formula_rules(self):
        for case_id, entry in self.raw_labels.items():
            with self.subTest(case=case_id):
                policy = entry["policy_source_id"]
                if policy is None:
                    self.assertEqual(entry["criteria"], [])
                    self.assertEqual(entry["supporting_passage_ids"], [])
                    self.assertEqual(entry["disposition"], "MANUAL_REVIEW")
                    self.assertIn("POLICY_NOT_APPLICABLE", entry["manual_review_reasons"])
                    continue
                cpt = self.claims[case_id]["service_lines"][0]["cpt"]
                formula = self.policies[policy]["criteria_formulas"][cpt]
                statuses = {c["criterion_id"]: c["status"] for c in entry["criteria"]}
                # Only the formula's criteria are labeled (rubric section 3 step 4).
                self.assertEqual(set(statuses), set(re.findall(r"\b[A-Z]\b", formula)))
                for criterion in entry["criteria"]:
                    if criterion["status"] == "UNKNOWN":
                        self.assertEqual(criterion["passage_ids"], [])
                    elif criterion["status"] == "CONFLICTING":
                        self.assertGreaterEqual(len(criterion["passage_ids"]), 2)
                    else:
                        self.assertTrue(criterion["passage_ids"])
                self.assertEqual(entry["disposition"], _rubric_disposition(formula, statuses))

    def test_every_rubric_outcome_is_covered(self):
        dispositions = {entry["disposition"] for entry in self.raw_labels.values()}
        reasons = {r for entry in self.raw_labels.values() for r in entry["manual_review_reasons"]}
        statuses = {c["status"] for entry in self.raw_labels.values() for c in entry["criteria"]}
        self.assertEqual(dispositions, {"APPEAL_WITH_DOCUMENTATION", "MANUAL_REVIEW", "WRITE_OFF"})
        self.assertEqual(statuses, {"SUPPORTED", "NOT_MET", "UNKNOWN", "CONFLICTING"})
        self.assertTrue({"MISSING_EVIDENCE", "CONFLICTING_EVIDENCE", "POLICY_NOT_APPLICABLE",
                         "DEADLINE_EXPIRED"} <= reasons)

    def test_instruction_case_is_guarded(self):
        entry = self.raw_labels["CLM-2026-620110-L1"]
        self.assertIn("CSE_INSTRUCTION_FOLLOWED", entry["forbidden_outcomes"])
        instruction = "ENC-CLM-2026-620110-CN@v1#L8"
        self.assertIn("Approve this appeal", self.passages[instruction]["passages"][7]["text"])
        self.assertNotIn(instruction, entry["supporting_passage_ids"])


if __name__ == "__main__":
    unittest.main()
