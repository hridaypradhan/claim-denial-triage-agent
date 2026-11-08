"""Checks that the Dataset B evidence in knowledge-v1 supports its planned cases.

The case list is in issue #12. These tests cover the retrieval side only:
each case's notes resolve for its claim, and policy matching by payer,
procedure, and service date gives the planned policy or NO_MATCH.
"""

import unittest
from datetime import date
from pathlib import Path

from claim_agent.tools.encounter_notes import (
    claim_document_inventory,
    find_encounter_note,
)
from claim_agent.tools.knowledge_store import KnowledgeStore
from claim_agent.tools.policy_retrieval import (
    PolicyRetrievalStatus,
    retrieve_policy_passages,
)

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"

# claim_id -> the encounter documents the case's claim should have.
CASE_DOCUMENTS = {
    "CLM-2026-620102": {"ENC-CLM-2026-620102-CN"},
    "CLM-2026-620103": {"ENC-CLM-2026-620103-CN", "ENC-CLM-2026-620103-RAD"},
    "CLM-2026-620104": {"ENC-CLM-2026-620104-CN"},
    "CLM-2026-620105": {"ENC-CLM-2026-620105-CN"},
    "CLM-2026-620106": {"ENC-CLM-2026-620106-CN"},
    "CLM-2026-620107": {"ENC-CLM-2026-620107-PT"},
    "CLM-2026-620108": {"ENC-CLM-2026-620108-PT"},
    "CLM-2026-620109": {"ENC-CLM-2026-620109-CN", "ENC-CLM-2026-620109-NN"},
    "CLM-2026-620110": {"ENC-CLM-2026-620110-CN"},
    "CLM-2026-620111": {"ENC-CLM-2026-620111-CN"},
    "CLM-2026-620112": {"ENC-CLM-2026-620112-CN"},
}

SERVICE_DATES = {
    "CLM-2026-620102": date(2026, 6, 9),
    "CLM-2026-620103": date(2026, 6, 16),
    "CLM-2026-620104": date(2026, 6, 23),
    "CLM-2026-620105": date(2026, 6, 4),
    "CLM-2026-620106": date(2026, 6, 11),
    "CLM-2026-620107": date(2026, 3, 12),
    "CLM-2026-620108": date(2026, 4, 14),
    "CLM-2026-620109": date(2026, 6, 18),
    "CLM-2026-620110": date(2026, 6, 25),
    "CLM-2026-620111": date(2026, 6, 30),
    "CLM-2026-620112": date(2025, 11, 14),
}

# (payer, procedure code, service date) -> expected policy "<id>@v<version>",
# or None when no policy may be used.
CASE_POLICIES = {
    "CLM-2026-620102": ("BCBS_AZ", "70553", date(2026, 6, 9), "POL-BCBSAZ-MRI-BRAIN@v1"),
    "CLM-2026-620105": ("BCBS_AZ", "70551", date(2026, 6, 4), "POL-BCBSAZ-MRI-BRAIN@v1"),
    "CLM-2026-620107": ("AETNA", "97110", date(2026, 3, 12), "POL-AETNA-PT-THERAPY@v1"),
    "CLM-2026-620108": ("AETNA", "97110", date(2026, 4, 14), "POL-AETNA-PT-THERAPY@v2"),
    "CLM-2026-620111": ("UHC", "70553", date(2026, 6, 30), None),
    "CLM-2026-620112": ("BCBS_AZ", "70553", date(2025, 11, 14), None),
}


class DatasetBEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = KnowledgeStore(KNOWLEDGE)

    def test_each_case_has_exactly_its_planned_documents(self):
        for claim_id, expected in CASE_DOCUMENTS.items():
            with self.subTest(claim=claim_id):
                inventory = claim_document_inventory(self.store, claim_id)
                self.assertEqual({d.document_id for d in inventory}, expected)

    def test_each_note_resolves_with_citable_passages(self):
        for claim_id, documents in CASE_DOCUMENTS.items():
            for document_id in documents:
                with self.subTest(document=document_id):
                    note = find_encounter_note(self.store, claim_id, document_id)
                    self.assertIsNotNone(note)
                    self.assertTrue(note.passages)
                    for index, passage in enumerate(note.passages, start=1):
                        self.assertEqual(
                            passage.passage_id, f"{document_id}@v1#L{index}"
                        )

    def test_a_note_is_unavailable_to_another_claim(self):
        self.assertIsNone(
            find_encounter_note(
                self.store, "CLM-2026-620104", "ENC-CLM-2026-620110-CN"
            )
        )

    def test_notes_are_dated_on_or_before_service(self):
        for claim_id, service_date in SERVICE_DATES.items():
            for document_id in CASE_DOCUMENTS[claim_id]:
                with self.subTest(document=document_id):
                    note = find_encounter_note(self.store, claim_id, document_id)
                    self.assertLessEqual(
                        date.fromisoformat(note.note_date), service_date
                    )

    def test_policy_matching_follows_payer_procedure_and_date(self):
        for claim_id, (payer, code, service_date, expected) in CASE_POLICIES.items():
            with self.subTest(claim=claim_id):
                result = retrieve_policy_passages(
                    self.store, payer, code, service_date, "criteria"
                )
                if expected is None:
                    self.assertEqual(result.status, PolicyRetrievalStatus.NO_MATCH)
                    self.assertIsNone(result.policy)
                else:
                    self.assertEqual(result.status, PolicyRetrievalStatus.OK)
                    policy = result.policy
                    self.assertEqual(
                        f"{policy.source_id}@v{policy.version}", expected
                    )


if __name__ == "__main__":
    unittest.main()
