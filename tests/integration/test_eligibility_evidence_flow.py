"""Eligibility evidence joins the run's evidence and resolves as citations (#56)."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from claim_agent.agent.run_evidence import RunEvidenceStore
from claim_agent.agent.tool_result_adapter import ToolResultDispatcherAdapter
from claim_agent.contracts import ClaimEvidenceScope, CitationProblemCode
from claim_agent.contracts.evidence_bundle import EvidenceBundle
from claim_agent.tools.dispatcher import ToolDispatcher
from claim_agent.tools.eligibility import ELIGIBILITY_ROOT, ELIGIBILITY_TOOL, EligibilityToolDispatcher
from claim_agent.tools.evidence_bundle import EvidenceBundleError, EvidenceBundleRecorder
from claim_agent.tools.knowledge_store import KnowledgeStore

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"
SCOPE = ClaimEvidenceScope(
    claim_id="CLM-2026-610104", service_line_id=1, payer="UHC",
    procedure_code="99213", service_date=date(2026, 6, 18),
)


class EligibilityEvidenceFlowTest(unittest.TestCase):
    def setUp(self):
        eligibility = EligibilityToolDispatcher(
            ToolDispatcher(KnowledgeStore(KNOWLEDGE)), KnowledgeStore(ELIGIBILITY_ROOT), "PA-SYN-10104",
        )
        self.recorder = EvidenceBundleRecorder(eligibility, "RUN-ELIG", "knowledge-v2", SCOPE)
        self.store = RunEvidenceStore()
        self.adapter = ToolResultDispatcherAdapter(
            dispatcher=self.recorder, observation_consumer=self.store.record_observation,
        )

    def ask(self, payer, request_id):
        return self.adapter.dispatch(request_id, ELIGIBILITY_TOOL, {"patient_account": "PA-SYN-10104", "payer": payer})

    def test_passages_join_the_run_and_the_bundle(self):
        self.assertTrue(self.ask("UHC", "REQ-1").succeeded)
        self.assertTrue(self.ask("CIGNA", "REQ-2").succeeded)

        stored = {p.passage_id for p in self.store.evidence_passages}
        bundle = self.recorder.freeze()
        self.assertEqual(stored, set(bundle.passage_ids))
        self.assertIn("ELIG-PA-SYN-10104-CIGNA@v1#E2", stored)
        self.assertEqual({p.source_type for p in bundle.passages}, {"eligibility_record"})

    def test_citations_resolve_only_for_retrieved_eligibility_evidence(self):
        self.ask("UHC", "REQ-1")
        bundle = self.recorder.freeze()

        resolved = bundle.resolve(["ELIG-PA-SYN-10104-UHC@v1#E2"])
        self.assertEqual(resolved[0].request_id, "REQ-1")
        for unretrieved in ("ELIG-PA-SYN-10104-CIGNA@v1#E2", "ELIG-PA-SYN-10105-UHC@v1#E2"):
            with self.subTest(citation=unretrieved):
                problems = bundle.check_citations([unretrieved])
                self.assertEqual([p.code for p in problems], [CitationProblemCode.UNRESOLVED_CITATION])

    def test_the_bundle_records_the_eligibility_snapshot(self):
        self.ask("UHC", "REQ-1")
        bundle = self.recorder.freeze()

        self.assertEqual(bundle.retrievals[0].source_snapshot_id, "eligibility-v1")
        self.assertEqual(bundle.snapshot_ids, ("knowledge-v2", "eligibility-v1"))
        self.assertIn('"source_snapshot_id":"eligibility-v1"', bundle.model_dump_json())

    def test_a_bundle_cannot_hold_eligibility_evidence_without_its_snapshot(self):
        self.ask("UHC", "REQ-1")
        data = self.recorder.freeze().model_dump(mode="json")
        data["retrievals"][0].pop("source_snapshot_id")

        with self.assertRaisesRegex(ValueError, "no eligibility snapshot"):
            EvidenceBundle.model_validate_json(json.dumps(data))

    def test_the_recorder_rejects_eligibility_evidence_without_a_snapshot(self):
        real = EligibilityToolDispatcher(
            ToolDispatcher(KnowledgeStore(KNOWLEDGE)), KnowledgeStore(ELIGIBILITY_ROOT), "PA-SYN-10104",
        )

        class DropsSnapshot:
            def dispatch(self, request_id, tool_name, tool_arguments):
                result = real.dispatch(request_id=request_id, tool_name=tool_name, tool_arguments=tool_arguments)
                data = {k: v for k, v in result.data.items() if k != "eligibility_snapshot_id"}
                return result.model_copy(update={"data": data})

        recorder = EvidenceBundleRecorder(DropsSnapshot(), "RUN-ELIG", "knowledge-v2", SCOPE)
        with self.assertRaises(EvidenceBundleError):
            recorder.dispatch("REQ-1", ELIGIBILITY_TOOL, {"patient_account": "PA-SYN-10104", "payer": "UHC"})
        self.assertEqual(recorder.freeze().retrievals, ())

    def test_a_no_match_adds_nothing(self):
        self.assertFalse(self.ask("AETNA", "REQ-1").succeeded)
        self.assertEqual(self.store.evidence_passages, ())
        self.assertEqual(self.recorder.freeze().retrievals[0].error_code, "NO_MATCH")


if __name__ == "__main__":
    unittest.main()
