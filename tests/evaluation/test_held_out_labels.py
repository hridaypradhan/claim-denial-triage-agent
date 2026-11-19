"""Structure and freeze checks for the held-out labels (#63).

These check that each label file matches its batch of the held-out manifest,
follows the rubric's mechanical rules, and still matches the freeze lock. They
never read system output.
"""

import dataclasses
import hashlib
import json
import re
import unittest
from datetime import date
from pathlib import Path

from evaluation.final_experiment import load_plan, prepare_experiment
from evaluation.pilot_data import load_dataset, load_labels

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "datasets" / "held_out" / "manifest.json"
KNOWLEDGE = REPO_ROOT / "knowledge"
LABEL_FILES = {
    "A": REPO_ROOT / "datasets" / "labels" / "held_out_batch_a.primary.json",
    "B": REPO_ROOT / "datasets" / "labels" / "held_out_batch_b.primary.json",
    "C": REPO_ROOT / "datasets" / "labels" / "held_out_batch_c.primary.json",
}


LOCK = REPO_ROOT / "datasets" / "labels" / "HELD_OUT_LABELS_LOCK.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_sha256(value: object) -> str:
    """The lock's hash: parsed JSON re-serialized with sorted keys, so line endings don't matter."""

    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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

    def test_frozen_labels_match_their_lock(self):
        """Any change to a frozen label file, including a re-serialized edit, fails here."""

        lock = read_json(LOCK)
        self.assertEqual(lock["cases"], len(self.manifest["cases"]))
        self.assertEqual(lock["knowledge_snapshot_id"], self.manifest["knowledge_snapshot_id"])
        self.assertEqual({f["path"] for f in lock["files"]},
                         {p.relative_to(REPO_ROOT).as_posix() for p in LABEL_FILES.values()})
        for entry in lock["files"]:
            with self.subTest(file=entry["path"]):
                raw = read_json(REPO_ROOT / entry["path"])
                self.assertEqual(raw["status"], "FROZEN")
                self.assertEqual(raw["review"]["pull_request"], entry["review_pull_request"])
                self.assertEqual(raw["review"]["reviewer"], entry["reviewer"])
                self.assertEqual(len(raw["cases"]), entry["cases"])
                self.assertEqual(canonical_sha256(raw), entry["canonical_sha256"])

    def test_final_experiment_plan_accepts_the_frozen_labels(self):
        """The final run's preflight passes on exactly these frozen files. Nothing is run."""

        prepared = prepare_experiment(load_plan(REPO_ROOT / "evaluation" / "final_experiment_plan.json"))
        self.assertEqual(len(prepared.labels), len(self.manifest["cases"]))

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
