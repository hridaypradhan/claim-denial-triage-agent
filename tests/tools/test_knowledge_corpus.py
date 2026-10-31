"""Integrity checks for the frozen knowledge corpus in knowledge/."""

import json
import re
import unittest
from pathlib import Path

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"
FORMULA_OPERATORS = {"AND", "OR"}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class KnowledgeCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_json(KNOWLEDGE / "manifest.json")
        cls.sources = [
            (entry, load_json(KNOWLEDGE / entry["path"]))
            for entry in cls.manifest["sources"]
            if (KNOWLEDGE / entry["path"]).is_file()
        ]

    def test_manifest_paths_exist(self):
        for entry in self.manifest["sources"]:
            with self.subTest(source=entry["source_id"]):
                self.assertTrue((KNOWLEDGE / entry["path"]).is_file(), entry["path"])

    def test_manifest_source_ids_are_unique(self):
        ids = [(e["source_id"], e["version"]) for e in self.manifest["sources"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_manifest_matches_documents(self):
        for entry, doc in self.sources:
            with self.subTest(source=entry["source_id"]):
                for field in ("source_id", "version", "source_type"):
                    self.assertEqual(entry[field], doc[field], field)

    def test_passage_ids_are_unique_and_well_formed(self):
        seen = set()
        for _, doc in self.sources:
            prefix = f'{doc["source_id"]}@v{doc["version"]}#'
            for passage in doc["passages"]:
                pid = passage["passage_id"]
                with self.subTest(passage=pid):
                    self.assertTrue(pid.startswith(prefix))
                    self.assertNotIn(pid, seen)
                    seen.add(pid)

    def test_criteria_reference_real_passages(self):
        for _, doc in self.sources:
            passage_ids = {p["passage_id"] for p in doc["passages"]}
            for criterion in doc.get("criteria", []):
                with self.subTest(criterion=criterion["criterion_id"]):
                    self.assertIn(criterion["passage_id"], passage_ids)

    def test_every_policy_code_has_a_valid_formula(self):
        for entry, doc in self.sources:
            if doc["source_type"] != "medical_policy":
                continue
            criterion_ids = {c["criterion_id"] for c in doc["criteria"]}
            formulas = doc["criteria_formulas"]
            self.assertEqual(set(formulas), set(entry["procedure_codes"]))
            for code, formula in formulas.items():
                with self.subTest(policy=doc["source_id"], code=code):
                    tokens = re.findall(r"[A-Za-z_]+", formula)
                    self.assertTrue(tokens)
                    for token in tokens:
                        self.assertIn(token, criterion_ids | FORMULA_OPERATORS)
                    self.assertEqual(formula.count("("), formula.count(")"))


if __name__ == "__main__":
    unittest.main()
