"""Structure checks for the held-out primary labels (#63).

These check that each label file matches its batch of the held-out manifest
and follows the rubric's mechanical rules. They never read system output.
"""

import dataclasses
import json
import re
import unittest
from datetime import date
from pathlib import Path

from evaluation.pilot_data import load_dataset, load_labels

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "datasets" / "held_out" / "manifest.json"
KNOWLEDGE = REPO_ROOT / "knowledge"
LABEL_FILES = {
    "A": REPO_ROOT / "datasets" / "labels" / "held_out_batch_a.primary.json",
    "B": REPO_ROOT / "datasets" / "labels" / "held_out_batch_b.primary.json",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def knowledge_sources() -> dict:
    sources = {}
    for entry in read_json(KNOWLEDGE / "manifest.json")["sources"]:
        sources[f'{entry["source_id"]}@v{entry["version"]}'] = read_json(KNOWLEDGE / entry["path"])
    return sources


class HeldOutPrimaryLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = read_json(MANIFEST)
        cls.dataset = load_dataset(MANIFEST)
        cls.sources = knowledge_sources()
        cls.passages = {p["passage_id"] for doc in cls.sources.values() for p in doc.get("passages", [])}

    def batch_dataset(self, batch):
        ids = {c["case_id"] for c in self.manifest["cases"] if c["batch"] == batch}
        return dataclasses.replace(self.dataset, cases=tuple(c for c in self.dataset.cases if c.case_id in ids))

    def test_each_file_has_exactly_its_batch(self):
        for batch, path in LABEL_FILES.items():
            with self.subTest(batch=batch):
                raw = read_json(path)
                self.assertEqual(raw["batch"], batch)
                self.assertEqual(raw["knowledge_snapshot_id"], self.manifest["knowledge_snapshot_id"])
                labels = load_labels(path, self.batch_dataset(batch))
                self.assertEqual(len(labels), sum(c["batch"] == batch for c in self.manifest["cases"]))

    def test_evidence_fields_follow_the_rubric(self):
        for batch, path in LABEL_FILES.items():
            for case in read_json(path)["cases"]:
                with self.subTest(case=case["case_id"]):
                    policy = case["policy_source_id"]
                    if policy is None:
                        self.assertEqual(case["criteria"], [])
                        self.assertEqual(case["supporting_passage_ids"], [])
                        continue
                    doc = self.sources[policy]
                    claim = read_json(REPO_ROOT / next(
                        c["claim_path"] for c in self.manifest["cases"] if c["case_id"] == case["case_id"]))
                    line = next(l for l in claim["service_lines"] if l["line"] == case["service_line_id"])
                    service = date.fromisoformat(claim["date_of_service"])
                    self.assertEqual(doc["payer"], claim["payer"])
                    self.assertLessEqual(date.fromisoformat(doc["effective_from"]), service)
                    if doc["effective_to"]:
                        self.assertLessEqual(service, date.fromisoformat(doc["effective_to"]))

                    formula = doc["criteria_formulas"][line["cpt"]]
                    criteria = {c["criterion_id"]: c for c in case["criteria"]}
                    self.assertEqual(set(criteria), set(re.findall(r"\b[A-Z]\b", formula)))
                    policy_passage = {c["criterion_id"]: c["passage_id"] for c in doc["criteria"]}
                    expected_support = set()
                    for cid, criterion in criteria.items():
                        ids = criterion["passage_ids"]
                        self.assertEqual(criterion["status"] == "UNKNOWN", not ids)
                        if criterion["status"] == "CONFLICTING":
                            self.assertGreaterEqual(len(ids), 2)
                        self.assertTrue(set(ids) <= self.passages)
                        if ids:
                            expected_support |= set(ids) | {policy_passage[cid]}
                    self.assertEqual(set(case["supporting_passage_ids"]), expected_support)

                    expr = formula
                    for cid, criterion in criteria.items():
                        expr = re.sub(rf"\b{cid}\b", str(criterion["status"] == "SUPPORTED"), expr)
                    holds = eval(expr.replace("AND", "and").replace("OR", "or"))  # noqa: S307 - fixed tokens
                    statuses = {c["status"] for c in criteria.values()}
                    if case["disposition"] == "APPEAL_WITH_DOCUMENTATION":
                        self.assertTrue(holds)
                    elif holds:
                        self.assertEqual(case["manual_review_reasons"], ["DEADLINE_EXPIRED"])
                    elif statuses & {"UNKNOWN", "CONFLICTING"}:
                        self.assertEqual(case["disposition"], "MANUAL_REVIEW")
                    else:
                        self.assertIn(case["disposition"], ("WRITE_OFF", "BILL_PATIENT"))

    def test_manual_review_and_deadline_fields_agree(self):
        for batch, path in LABEL_FILES.items():
            for case in read_json(path)["cases"]:
                with self.subTest(case=case["case_id"]):
                    manual = case["disposition"] == "MANUAL_REVIEW"
                    self.assertEqual(bool(case["manual_review_reasons"]), manual)
                    has_deadline = case["disposition"] in (
                        "APPEAL_WITH_DOCUMENTATION", "CORRECT_AND_RESUBMIT", "ROUTE_TO_CORRECT_PAYER")
                    self.assertEqual(case["deadline"] is not None, has_deadline)
                    if has_deadline:
                        self.assertGreaterEqual(case["deadline"], self.manifest["as_of"])


if __name__ == "__main__":
    unittest.main()
