"""Tests for same-run evidence bundles and citation resolution (#59)."""

import unittest
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from claim_agent.agent.tool_request_guard import (
    ClaimBoundToolDispatcher,
    ClaimToolContext,
    ToolArgumentMismatchError,
)
from claim_agent.contracts import EvidencePassage, ToolResult, ToolStatus
from claim_agent.tools.dispatcher import ENCOUNTER_NOTE_TOOL, ToolDispatcher
from claim_agent.tools.evidence_bundle import (
    CitationProblemCode,
    CitationResolutionError,
    ClaimEvidenceScope,
    EvidenceBundle,
    EvidenceBundleError,
    EvidenceBundleRecorder,
    source_claim_index,
)
from claim_agent.tools.knowledge_store import KnowledgeStore
from evidence_bundle_fixtures import FIXTURE_BUILDERS, FIXTURES, appeal_bundle, no_policy_bundle, render

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"
SCOPE = ClaimEvidenceScope(
    claim_id="CLM-1", service_line_id=1, payer="BCBS_AZ", procedure_code="70553", service_date=date(2026, 6, 2)
)


def passage(passage_id="ENC-CLM-1@v1#L1", text="note text"):
    source_id, rest = passage_id.split("@v")
    return EvidencePassage(
        passage_id=passage_id, source_id=source_id, source_type="encounter_note",
        version=rest.split("#")[0], text=text,
    )


class FakeDispatcher:
    """Returns queued results, echoing each request's identity."""

    def __init__(self, *results):
        self.results = list(results)

    def dispatch(self, request_id, tool_name, tool_arguments):
        status, passages, data = self.results.pop(0)
        if status is ToolStatus.OK:
            source_ids = list(dict.fromkeys(f"{p.source_id}@v{p.version}" for p in passages))
            return ToolResult(request_id=request_id, tool_name=tool_name, status=status,
                              data=data, source_ids=source_ids, evidence_passages=list(passages))
        return ToolResult(request_id=request_id, tool_name=tool_name, status=status, error_code="NO_MATCH")


def recorder(*results):
    return EvidenceBundleRecorder(FakeDispatcher(*results), "RUN-1", "knowledge-v1", SCOPE)


class FixtureTest(unittest.TestCase):
    def test_committed_fixtures_match_a_fresh_retrieval(self):
        for name, build in FIXTURE_BUILDERS.items():
            with self.subTest(fixture=name):
                self.assertEqual((FIXTURES / name).read_text(encoding="utf-8"), render(build()))

    def test_fixtures_load_as_bundles(self):
        for name in FIXTURE_BUILDERS:
            with self.subTest(fixture=name):
                bundle = EvidenceBundle.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))
                self.assertEqual(bundle.knowledge_snapshot_id, "knowledge-v1")

    def test_no_match_call_is_recorded_without_evidence(self):
        bundle = no_policy_bundle()
        self.assertEqual(bundle.passages, ())
        self.assertIsNone(bundle.policy_formula)
        (call,) = bundle.retrievals
        self.assertEqual((call.status, call.error_code), (ToolStatus.ERROR, "NO_MATCH"))


class ResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = appeal_bundle()
        cls.claims = source_claim_index(KnowledgeStore(KNOWLEDGE).entries_of_type("encounter_note"))

    def test_resolves_in_citation_order_with_provenance(self):
        cited = ["ENC-CLM-2026-005097-PN@v1#L4", "ENC-CLM-2026-005097-CN@v1#L3", "POL-BCBSAZ-MRI-BRAIN@v1#P5"]
        resolved = self.bundle.resolve(cited, self.claims)

        self.assertEqual([r.passage.passage_id for r in resolved], cited)
        self.assertEqual([r.call_index for r in resolved], [3, 2, 1])
        self.assertTrue(all(r.claim_id == "CLM-2026-005097" for r in resolved))

    def test_each_rejection_has_its_own_code(self):
        cases = {
            CitationProblemCode.DUPLICATE_CITATION: ["ENC-CLM-2026-005097-CN@v1#L3"] * 2,
            CitationProblemCode.MALFORMED_CITATION: ["CN line 3"],
            CitationProblemCode.UNRESOLVED_CITATION: ["ENC-CLM-2026-005097-CN@v1#L99"],
            CitationProblemCode.CROSS_CLAIM_CITATION: ["ENC-CLM-2026-620110-CN@v1#L8"],
        }
        for code, cited in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(CitationResolutionError) as caught:
                    self.bundle.resolve(cited, self.claims)
                self.assertEqual([p.code for p in caught.exception.problems], [code])

    def test_without_a_claim_index_other_claims_are_unresolved(self):
        problems = self.bundle.check_citations(["ENC-CLM-2026-620110-CN@v1#L8"])
        self.assertEqual([p.code for p in problems], [CitationProblemCode.UNRESOLVED_CITATION])

    def test_check_reports_every_problem_with_its_index(self):
        problems = self.bundle.check_citations(
            ["ENC-CLM-2026-005097-CN@v1#L3", "bad", "ENC-CLM-2026-005097-CN@v1#L3"], self.claims
        )
        self.assertEqual([(p.citation_index, p.code) for p in problems], [
            (1, CitationProblemCode.MALFORMED_CITATION),
            (2, CitationProblemCode.DUPLICATE_CITATION),
        ])

    def test_empty_citations_resolve_to_nothing(self):
        self.assertEqual(self.bundle.resolve([]), ())


class ImmutabilityTest(unittest.TestCase):
    def test_bundle_cannot_be_changed(self):
        bundle = appeal_bundle()
        with self.assertRaises(ValidationError):
            bundle.run_id = "OTHER"

    def test_freeze_returns_one_bundle_and_stops_recording(self):
        rec = recorder((ToolStatus.OK, [passage()], {}), (ToolStatus.OK, [], {}))
        rec.dispatch("REQ-1", ENCOUNTER_NOTE_TOOL, {})

        self.assertIs(rec.freeze(), rec.freeze())
        with self.assertRaises(EvidenceBundleError):
            rec.dispatch("REQ-2", ENCOUNTER_NOTE_TOOL, {})

    def test_fingerprint_is_stable_and_content_sensitive(self):
        self.assertEqual(appeal_bundle().fingerprint(), appeal_bundle().fingerprint())
        self.assertNotEqual(appeal_bundle().fingerprint(), no_policy_bundle().fingerprint())


class RecorderSafetyTest(unittest.TestCase):
    def test_evidence_reported_for_another_claim_is_rejected(self):
        rec = recorder((ToolStatus.OK, [passage()], {"claim_id": "CLM-2"}))
        with self.assertRaises(EvidenceBundleError):
            rec.dispatch("REQ-1", ENCOUNTER_NOTE_TOOL, {})

    def test_evidence_from_another_snapshot_is_rejected(self):
        rec = recorder((ToolStatus.OK, [passage()], {"knowledge_snapshot_id": "knowledge-v0"}))
        with self.assertRaises(EvidenceBundleError):
            rec.dispatch("REQ-1", ENCOUNTER_NOTE_TOOL, {})

    def test_conflicting_content_for_one_passage_is_rejected(self):
        rec = recorder((ToolStatus.OK, [passage(text="a")], {}), (ToolStatus.OK, [passage(text="b")], {}))
        rec.dispatch("REQ-1", ENCOUNTER_NOTE_TOOL, {})
        with self.assertRaises(EvidenceBundleError):
            rec.dispatch("REQ-2", ENCOUNTER_NOTE_TOOL, {})

    def test_repeated_passage_keeps_its_first_provenance(self):
        rec = recorder((ToolStatus.OK, [passage()], {}), (ToolStatus.OK, [passage()], {}))
        rec.dispatch("REQ-1", ENCOUNTER_NOTE_TOOL, {})
        rec.dispatch("REQ-2", ENCOUNTER_NOTE_TOOL, {})
        bundle = rec.freeze()

        self.assertEqual(len(bundle.passages), 1)
        self.assertEqual(bundle.passages[0].request_id, "REQ-1")
        self.assertEqual(len(bundle.retrievals), 2)

    def test_conflicting_policy_formulas_are_rejected(self):
        rec = recorder((ToolStatus.OK, [], {"criteria_formula": "A"}), (ToolStatus.OK, [], {"criteria_formula": "B"}))
        rec.dispatch("REQ-1", ENCOUNTER_NOTE_TOOL, {})
        with self.assertRaises(EvidenceBundleError):
            rec.dispatch("REQ-2", ENCOUNTER_NOTE_TOOL, {})

    def test_bundle_rejects_passages_outside_its_scope(self):
        bundle = appeal_bundle()
        other = bundle.passages[0].model_copy(update={"claim_id": "CLM-OTHER"})
        with self.assertRaises(ValidationError):
            EvidenceBundle(run_id="R", knowledge_snapshot_id="k", scope=bundle.scope,
                           passages=(other,), retrievals=bundle.retrievals)

    def test_cross_claim_request_is_refused_before_anything_is_recorded(self):
        store = KnowledgeStore(KNOWLEDGE)
        context = ClaimToolContext(claim_id="CLM-2026-005097", payer="BCBS_AZ",
                                   procedure_code="70553", service_date=date(2026, 6, 2))
        scope = ClaimEvidenceScope(claim_id="CLM-2026-005097", service_line_id=1, payer="BCBS_AZ",
                                   procedure_code="70553", service_date=date(2026, 6, 2))
        rec = EvidenceBundleRecorder(ClaimBoundToolDispatcher(ToolDispatcher(store), context),
                                     "RUN-1", store.snapshot_id, scope)

        with self.assertRaises(ToolArgumentMismatchError):
            rec.dispatch("REQ-1", ENCOUNTER_NOTE_TOOL,
                         {"claim_id": "CLM-2026-620110", "document_id": "ENC-CLM-2026-620110-CN"})
        self.assertEqual(rec.freeze().retrievals, ())


if __name__ == "__main__":
    unittest.main()
