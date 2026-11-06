"""Tests for the document inventory and the get_encounter_note tool."""

import json
import tempfile
import unittest
from pathlib import Path

from claim_agent.contracts import ErrorCode, ToolResult, ToolStatus
from claim_agent.tools.dispatcher import ENCOUNTER_NOTE_TOOL, ToolDispatcher
from claim_agent.tools.encounter_notes import (
    claim_document_inventory,
    find_encounter_note,
)
from claim_agent.tools.knowledge_store import KnowledgeStore

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"
CLAIM_ID = "CLM-2026-005097"
CLINICAL_NOTE_ID = "ENC-CLM-2026-005097-CN"
PROGRESS_NOTE_ID = "ENC-CLM-2026-005097-PN"


def encounter_entry(source_id, version, claim_id):
    """Build one encounter-note manifest entry."""

    return {
        "source_id": source_id,
        "source_type": "encounter_note",
        "version": version,
        "path": f"{source_id}.v{version}.json",
        "claim_id": claim_id,
        "document_type": "clinical_note",
    }


def write_manifest(root, entries):
    """Write a manifest listing the given entries."""

    manifest = {"snapshot_id": "test-snapshot", "sources": entries}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return KnowledgeStore(root)


class DocumentInventoryTest(unittest.TestCase):
    def setUp(self):
        self.store = KnowledgeStore(KNOWLEDGE)

    def test_inventory_lists_the_claims_documents(self):
        inventory = claim_document_inventory(self.store, CLAIM_ID)

        self.assertEqual(
            [document.document_id for document in inventory],
            [CLINICAL_NOTE_ID, PROGRESS_NOTE_ID],
        )
        self.assertEqual(
            [document.document_type for document in inventory],
            ["clinical_note", "progress_notes"],
        )

    def test_unknown_claim_has_an_empty_inventory(self):
        self.assertEqual(
            claim_document_inventory(self.store, "CLM-UNKNOWN"),
            (),
        )

    def test_every_inventory_document_can_be_loaded(self):
        for document in claim_document_inventory(self.store, CLAIM_ID):
            with self.subTest(document=document.document_id):
                note = find_encounter_note(
                    self.store,
                    CLAIM_ID,
                    document.document_id,
                )

                self.assertEqual(note.document_type, document.document_type)


class EncounterNoteToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dispatcher = ToolDispatcher(KnowledgeStore(KNOWLEDGE))

    def dispatch(self, arguments):
        return self.dispatcher.dispatch(
            "REQ-2",
            ENCOUNTER_NOTE_TOOL,
            arguments,
        )

    def note_result(self, document_id=CLINICAL_NOTE_ID, claim_id=CLAIM_ID):
        return self.dispatch(
            {"claim_id": claim_id, "document_id": document_id},
        )

    def test_note_returns_ok_result_with_identity(self):
        result = self.note_result()

        self.assertIs(result.status, ToolStatus.OK)
        self.assertEqual(result.request_id, "REQ-2")
        self.assertEqual(result.tool_name, ENCOUNTER_NOTE_TOOL)
        self.assertEqual(result.source_ids, [f"{CLINICAL_NOTE_ID}@v1"])

    def test_passages_are_citable_and_in_document_order(self):
        passages = self.note_result().evidence_passages

        self.assertEqual(
            [passage.passage_id for passage in passages],
            [f"{CLINICAL_NOTE_ID}@v1#L{n}" for n in range(1, 7)],
        )
        self.assertEqual(passages[0].source_type, "encounter_note")
        self.assertIsNone(passages[0].effective_from)

    def test_data_describes_the_document(self):
        data = self.note_result(PROGRESS_NOTE_ID).data

        self.assertEqual(data["claim_id"], CLAIM_ID)
        self.assertEqual(data["document_type"], "progress_notes")
        self.assertEqual(data["note_date"], "2026-05-30")
        self.assertTrue(data["synthetic"])

    def test_result_survives_json_round_trip(self):
        result = self.note_result()

        self.assertEqual(
            ToolResult.model_validate_json(result.model_dump_json()),
            result,
        )

    def test_unknown_document_is_unavailable(self):
        result = self.note_result("ENC-MISSING")

        self.assertIs(result.error_code, ErrorCode.DOCUMENT_UNAVAILABLE)
        self.assertFalse(result.retryable)
        self.assertEqual(result.evidence_passages, [])

    def test_document_from_another_claim_is_unavailable(self):
        result = self.note_result(claim_id="CLM-2026-004812")

        self.assertIs(result.error_code, ErrorCode.DOCUMENT_UNAVAILABLE)
        self.assertEqual(result.evidence_passages, [])

    def test_policy_source_is_not_an_encounter_note(self):
        result = self.note_result("POL-BCBSAZ-MRI-BRAIN")

        self.assertIs(result.error_code, ErrorCode.DOCUMENT_UNAVAILABLE)

    def test_invalid_requests_are_malformed(self):
        invalid_requests = {
            "missing document": {"claim_id": CLAIM_ID},
            "blank claim": {"claim_id": " ", "document_id": CLINICAL_NOTE_ID},
            "number": {"claim_id": 5097, "document_id": CLINICAL_NOTE_ID},
            "extra argument": {
                "claim_id": CLAIM_ID,
                "document_id": CLINICAL_NOTE_ID,
                "service_line": 1,
            },
        }

        for name, arguments in invalid_requests.items():
            with self.subTest(request=name):
                result = self.dispatch(arguments)

                self.assertIs(result.error_code, ErrorCode.MALFORMED_REQUEST)


class EncounterCorpusFailureTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def dispatch(self, store):
        return ToolDispatcher(store).dispatch(
            "REQ-2",
            ENCOUNTER_NOTE_TOOL,
            {"claim_id": "CLM-1", "document_id": "ENC-1"},
        )

    def test_listed_but_missing_file_is_unavailable(self):
        store = write_manifest(
            self.root,
            [encounter_entry("ENC-1", "1", "CLM-1")],
        )

        self.assertIs(
            self.dispatch(store).error_code,
            ErrorCode.DOCUMENT_UNAVAILABLE,
        )

    def test_two_versions_are_unavailable_rather_than_guessed(self):
        store = write_manifest(
            self.root,
            [
                encounter_entry("ENC-1", "1", "CLM-1"),
                encounter_entry("ENC-1", "2", "CLM-1"),
            ],
        )

        self.assertIs(
            self.dispatch(store).error_code,
            ErrorCode.DOCUMENT_UNAVAILABLE,
        )


if __name__ == "__main__":
    unittest.main()
