"""Tests for tool-request validation and ToolResult conversion."""

import tempfile
import unittest
from pathlib import Path

from claim_agent.contracts import ErrorCode, ToolResult, ToolStatus
from claim_agent.tools.dispatcher import MEDICAL_POLICY_TOOL, ToolDispatcher
from claim_agent.tools.knowledge_store import KnowledgeStore
from test_policy_retrieval import policy_document, write_corpus

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"
VALID_ARGUMENTS = {
    "payer": "BCBS_AZ",
    "procedure_code": "70553",
    "service_date": "2026-06-02",
    "query": "contrast enhancement suspected intracranial mass",
}


class PolicyToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dispatcher = ToolDispatcher(KnowledgeStore(KNOWLEDGE))

    def dispatch(self, **overrides):
        arguments = {**VALID_ARGUMENTS, **overrides}
        return self.dispatcher.dispatch(
            "REQ-1",
            MEDICAL_POLICY_TOOL,
            arguments,
        )

    def test_match_returns_ok_result_with_identity(self):
        result = self.dispatch()

        self.assertIs(result.status, ToolStatus.OK)
        self.assertEqual(result.request_id, "REQ-1")
        self.assertEqual(result.tool_name, MEDICAL_POLICY_TOOL)
        self.assertEqual(result.source_ids, ["POL-BCBSAZ-MRI-BRAIN@v1"])

    def test_passages_keep_ranked_order(self):
        result = self.dispatch()

        self.assertEqual(
            result.evidence_passages[0].passage_id,
            "POL-BCBSAZ-MRI-BRAIN@v1#P5",
        )
        self.assertEqual(
            [p.passage_id for p in result.evidence_passages],
            [r["passage_id"] for r in result.data["passage_rankings"]],
        )

    def test_passages_carry_their_source(self):
        passage = self.dispatch().evidence_passages[0]

        self.assertEqual(passage.source_id, "POL-BCBSAZ-MRI-BRAIN")
        self.assertEqual(passage.source_type, "medical_policy")
        self.assertEqual(passage.version, "1")
        self.assertIsNone(passage.effective_to)

    def test_data_holds_criteria_for_the_procedure_code(self):
        data = self.dispatch(procedure_code="70551").data

        self.assertEqual(data["criteria_formula"], "A AND B")
        self.assertEqual(len(data["criteria"]), 4)
        self.assertEqual(data["knowledge_snapshot_id"], "knowledge-v0")

    def test_service_date_selects_the_policy_version(self):
        arguments = {"payer": "AETNA", "procedure_code": "97110"}

        self.assertEqual(
            self.dispatch(service_date="2026-03-31", **arguments).source_ids,
            ["POL-AETNA-PT-THERAPY@v1"],
        )
        self.assertEqual(
            self.dispatch(service_date="2026-04-01", **arguments).source_ids,
            ["POL-AETNA-PT-THERAPY@v2"],
        )

    def test_result_survives_json_round_trip(self):
        result = self.dispatch()

        self.assertEqual(
            ToolResult.model_validate_json(result.model_dump_json()),
            result,
        )

    def test_no_match_is_an_explicit_error(self):
        result = self.dispatch(payer="UHC")

        self.assertIs(result.status, ToolStatus.ERROR)
        self.assertIs(result.error_code, ErrorCode.NO_MATCH)
        self.assertFalse(result.retryable)
        self.assertEqual(result.evidence_passages, [])

    def test_invalid_requests_are_malformed(self):
        invalid_requests = {
            "blank query": {"query": "  "},
            "number instead of text": {"procedure_code": 70553},
            "impossible date": {"service_date": "2026-02-30"},
            "compact date": {"service_date": "20260602"},
            "week date": {"service_date": "2026-W23-2"},
            "date text": {"service_date": "June 2"},
            "extra argument": {"member_id": "M123"},
        }

        for name, overrides in invalid_requests.items():
            with self.subTest(request=name):
                result = self.dispatch(**overrides)

                self.assertIs(result.error_code, ErrorCode.MALFORMED_REQUEST)
                self.assertFalse(result.retryable)

    def test_missing_argument_is_malformed(self):
        arguments = dict(VALID_ARGUMENTS)
        del arguments["query"]

        result = self.dispatcher.dispatch(
            "REQ-1",
            MEDICAL_POLICY_TOOL,
            arguments,
        )

        self.assertIs(result.error_code, ErrorCode.MALFORMED_REQUEST)

    def test_unknown_tool_is_malformed(self):
        result = self.dispatcher.dispatch("REQ-1", "delete_claim", {})

        self.assertIs(result.status, ToolStatus.ERROR)
        self.assertIs(result.error_code, ErrorCode.MALFORMED_REQUEST)


class DispatcherCorpusFailureTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def dispatch(self, store):
        return ToolDispatcher(store).dispatch(
            "REQ-1",
            MEDICAL_POLICY_TOOL,
            VALID_ARGUMENTS,
        )

    def test_overlapping_versions_are_ambiguous(self):
        store = write_corpus(
            self.root,
            [
                policy_document("POL-TEST", version="1"),
                policy_document("POL-TEST", version="2"),
            ],
        )

        result = self.dispatch(store)

        self.assertIs(result.error_code, ErrorCode.AMBIGUOUS_POLICY)
        self.assertEqual(result.source_ids, [])

    def test_listed_code_without_formula_is_no_match(self):
        store = write_corpus(
            self.root,
            [policy_document("POL-TEST", formulas={"70551": "A"})],
        )

        self.assertIs(self.dispatch(store).error_code, ErrorCode.NO_MATCH)

    def test_unreadable_source_is_unavailable(self):
        document = policy_document("POL-TEST")
        store = write_corpus(self.root, [document])
        (self.root / "POL-TEST.v1.json").unlink()

        self.assertIs(
            self.dispatch(store).error_code,
            ErrorCode.DOCUMENT_UNAVAILABLE,
        )


if __name__ == "__main__":
    unittest.main()
