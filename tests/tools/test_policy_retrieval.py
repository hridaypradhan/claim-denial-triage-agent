"""Tests for policy matching, passage ranking, and the knowledge store."""

import json
import math
import tempfile
import unittest
from datetime import date
from pathlib import Path

from claim_agent.tools.bm25 import score_documents, tokenize
from claim_agent.tools.knowledge_store import (
    KnowledgeStore,
    KnowledgeStoreError,
)
from claim_agent.tools.policy_retrieval import (
    PolicyRetrievalStatus,
    retrieve_policy_passages,
)

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"
SERVICE_DATE = date(2026, 6, 2)


def policy_document(source_id, version="1", formulas=None, passages=None):
    """Build a small policy document for a temporary corpus."""

    return {
        "source_id": source_id,
        "source_type": "medical_policy",
        "version": version,
        "synthetic": True,
        "title": f"{source_id} title",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "criteria": [
            {
                "criterion_id": "A",
                "summary": "Criterion A",
                "passage_id": f"{source_id}@v{version}#P1",
            }
        ],
        "criteria_formulas": {"70553": "A"} if formulas is None else formulas,
        "passages": passages
        or [
            {
                "passage_id": f"{source_id}@v{version}#P1",
                "section": "1",
                "text": "first passage",
            }
        ],
    }


def write_corpus(root, documents, **entry_overrides):
    """Write documents and a matching manifest into a temporary corpus."""

    entries = []
    for document in documents:
        path = f'{document["source_id"]}.v{document["version"]}.json'
        (root / path).write_text(json.dumps(document), encoding="utf-8")
        entry = {
            "source_id": document["source_id"],
            "source_type": document["source_type"],
            "version": document["version"],
            "path": path,
            "payer": "BCBS_AZ",
            "procedure_codes": ["70553"],
            "effective_from": document["effective_from"],
            "effective_to": document["effective_to"],
        }
        entry.update(entry_overrides)
        entries.append(entry)

    manifest = {"snapshot_id": "test-snapshot", "sources": entries}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return KnowledgeStore(root)


class Bm25Test(unittest.TestCase):
    def test_tokenize_lowercases_and_splits_on_non_alphanumerics(self):
        self.assertEqual(
            tokenize("New-onset Seizure, CPT_70553!"),
            ["new", "onset", "seizure", "cpt", "70553"],
        )

    def test_score_matches_hand_calculated_value(self):
        scores = score_documents("b", ["a b", "a c"])

        self.assertAlmostEqual(scores[0], math.log(2))
        self.assertEqual(scores[1], 0.0)

    def test_repeated_query_terms_count_once(self):
        documents = ["a b", "a c"]

        self.assertEqual(
            score_documents("b b b", documents),
            score_documents("b", documents),
        )

    def test_documents_without_terms_score_zero(self):
        self.assertEqual(score_documents("a", ["", "..."]), (0.0, 0.0))


class RetrievalAgainstCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = KnowledgeStore(KNOWLEDGE)

    def retrieve(self, **overrides):
        request = {
            "payer": "BCBS_AZ",
            "procedure_code": "70553",
            "service_date": SERVICE_DATE,
            "query": "contrast enhancement suspected intracranial mass",
        }
        request.update(overrides)
        return retrieve_policy_passages(self.store, **request)

    def test_matching_claim_returns_the_policy(self):
        result = self.retrieve()

        self.assertIs(result.status, PolicyRetrievalStatus.OK)
        self.assertEqual(result.policy.source_id, "POL-BCBSAZ-MRI-BRAIN")
        self.assertEqual(
            result.matched_source_ids,
            ("POL-BCBSAZ-MRI-BRAIN@v1",),
        )

    def test_formula_is_selected_by_procedure_code(self):
        self.assertEqual(
            self.retrieve().policy.criteria_formula,
            "A AND B AND (C OR D)",
        )
        self.assertEqual(
            self.retrieve(procedure_code="70551").policy.criteria_formula,
            "A AND B",
        )

    def test_most_relevant_passage_ranks_first(self):
        passages = self.retrieve().policy.passages

        self.assertEqual(
            passages[0].passage_id,
            "POL-BCBSAZ-MRI-BRAIN@v1#P5",
        )
        self.assertGreater(passages[0].score, passages[1].score)

    def test_all_passages_and_criteria_survive_an_unrelated_query(self):
        policy = self.retrieve(query="zzz").policy

        self.assertEqual(len(policy.criteria), 4)
        self.assertEqual(len(policy.passages), 7)

    def test_ties_keep_file_order(self):
        passage_ids = [
            passage.passage_id
            for passage in self.retrieve(query="zzz").policy.passages
        ]

        self.assertEqual(
            passage_ids,
            [f"POL-BCBSAZ-MRI-BRAIN@v1#P{n}" for n in range(1, 8)],
        )

    def test_ranking_is_repeatable(self):
        self.assertEqual(self.retrieve(), self.retrieve())

    def test_filters_never_fall_back(self):
        mismatches = {
            "payer": {"payer": "AETNA"},
            "procedure": {"procedure_code": "70450"},
            "service_date": {"service_date": date(2025, 12, 31)},
        }

        for name, overrides in mismatches.items():
            with self.subTest(mismatch=name):
                result = self.retrieve(**overrides)

                self.assertIs(result.status, PolicyRetrievalStatus.NO_MATCH)
                self.assertIsNone(result.policy)


class RetrievalEdgeCaseTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def retrieve(self, store, service_date=SERVICE_DATE):
        return retrieve_policy_passages(
            store,
            payer="BCBS_AZ",
            procedure_code="70553",
            service_date=service_date,
            query="passage",
        )

    def test_effective_dates_are_inclusive(self):
        document = policy_document("POL-TEST")
        document["effective_to"] = "2026-06-30"
        store = write_corpus(self.root, [document])

        boundaries = {
            date(2025, 12, 31): PolicyRetrievalStatus.NO_MATCH,
            date(2026, 1, 1): PolicyRetrievalStatus.OK,
            date(2026, 6, 30): PolicyRetrievalStatus.OK,
            date(2026, 7, 1): PolicyRetrievalStatus.NO_MATCH,
        }

        for service_date, expected_status in boundaries.items():
            with self.subTest(service_date=service_date):
                result = self.retrieve(store, service_date)

                self.assertIs(result.status, expected_status)

    def test_overlapping_versions_are_ambiguous(self):
        store = write_corpus(
            self.root,
            [
                policy_document("POL-TEST", version="1"),
                policy_document("POL-TEST", version="2"),
            ],
        )

        result = self.retrieve(store)

        self.assertIs(result.status, PolicyRetrievalStatus.AMBIGUOUS_POLICY)
        self.assertIsNone(result.policy)
        self.assertEqual(
            result.matched_source_ids,
            ("POL-TEST@v1", "POL-TEST@v2"),
        )

    def test_version_is_chosen_by_service_date(self):
        old_version = policy_document("POL-TEST", version="1")
        old_version["effective_to"] = "2026-03-31"
        new_version = policy_document("POL-TEST", version="2")
        new_version["effective_from"] = "2026-04-01"
        store = write_corpus(self.root, [old_version, new_version])

        self.assertEqual(self.retrieve(store).policy.version, "2")
        self.assertEqual(
            self.retrieve(store, date(2026, 2, 1)).policy.version,
            "1",
        )

    def test_listed_code_without_formula_is_no_match(self):
        store = write_corpus(
            self.root,
            [policy_document("POL-TEST", formulas={"70551": "A"})],
        )

        self.assertIs(
            self.retrieve(store).status,
            PolicyRetrievalStatus.NO_MATCH,
        )

    def test_other_source_types_are_ignored(self):
        document = policy_document("ENC-TEST")
        document["source_type"] = "encounter_note"
        store = write_corpus(self.root, [document])

        self.assertIs(
            self.retrieve(store).status,
            PolicyRetrievalStatus.NO_MATCH,
        )


class KnowledgeStoreTest(unittest.TestCase):
    def test_snapshot_id_comes_from_the_manifest(self):
        self.assertEqual(KnowledgeStore(KNOWLEDGE).snapshot_id, "knowledge-v1")

    def test_missing_manifest_raises_store_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KnowledgeStoreError):
                KnowledgeStore(Path(directory))

    def test_missing_source_raises_store_error(self):
        store = KnowledgeStore(KNOWLEDGE)

        with self.assertRaises(KnowledgeStoreError):
            store.load_source({"path": "policies/missing.json"})

    def test_invalid_json_raises_store_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text("{", encoding="utf-8")

            with self.assertRaises(KnowledgeStoreError):
                KnowledgeStore(root)


if __name__ == "__main__":
    unittest.main()
