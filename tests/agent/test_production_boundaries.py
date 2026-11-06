"""Tests for assembling production runtime boundaries."""

from __future__ import annotations

import unittest
from datetime import date
from typing import Mapping

from claim_agent.agent.production_boundaries import (
    build_production_runtime_boundaries,
)
from claim_agent.agent.runtime_ports import OfflineValidationStatus
from claim_agent.contracts import (
    BaselineFinding,
    Disposition,
    EvidencePassage,
    RootCause,
    ToolResult,
    ToolStatus,
)
from claim_agent.agent.tool_request_guard import ClaimToolContext

CLAIM_ID = "CLM-BOUNDARY-001"
PASSAGE_ID = "ENC-BOUNDARY-001@v1#P1"
TOOL_NAME = "get_encounter_note"


class SyntheticEncounterDispatcher:
    """Return one encounter passage using the request identity received."""

    def dispatch(
        self,
        request_id: str,
        tool_name: str,
        tool_arguments: Mapping[str, object],
    ) -> ToolResult:
        """Return deterministic evidence for boundary-integration tests."""

        passage = EvidencePassage(
            passage_id=PASSAGE_ID,
            source_id="ENC-BOUNDARY-001",
            source_type="encounter_note",
            version="1",
            text="The encounter note supports the coding correction.",
        )

        return ToolResult(
            request_id=request_id,
            tool_name=tool_name,
            status=ToolStatus.OK,
            data={"claim_id": tool_arguments.get("claim_id")},
            source_ids=["ENC-BOUNDARY-001@v1"],
            evidence_passages=[passage],
        )


def make_baseline_finding() -> BaselineFinding:
    """Create the shared deterministic input for one synthetic claim."""

    return BaselineFinding(
        claim_id=CLAIM_ID,
        service_line_id=1,
        denial_codes=["CO-16"],
        root_cause=RootCause.CODING,
        baseline_disposition=Disposition.CORRECT_AND_RESUBMIT,
        required_evidence=["encounter_note"],
        rationale="The coding must be corrected before resubmission.",
        filing_deadline=date(2026, 12, 31),
        deadline_status="ACTIVE",
    )


def make_claim_tool_context(
    claim_id: str = CLAIM_ID,
) -> ClaimToolContext:
    """Create trusted retrieval fields for the synthetic claim."""

    return ClaimToolContext(
        claim_id=claim_id,
        payer="BCBS_AZ",
        procedure_code="70553",
        service_date=date(2026, 6, 2),
    )


def make_decision_payload() -> dict[str, object]:
    """Create a model-shaped proposal grounded in the encounter passage."""

    return {
        "decision_id": "DEC-BOUNDARY-001",
        "claim_id": CLAIM_ID,
        "service_line_id": 1,
        "root_cause": "CODING",
        "recommended_disposition": "CORRECT_AND_RESUBMIT",
        "actions": ["Correct the coding and resubmit the claim."],
        "criterion_assessments": [],
        "evidence_gaps": [],
        "agent_status": "COMPLETED",
        "manual_review_reasons": [],
        "rationale": "The encounter note supports correction.",
        "citation_ids": [PASSAGE_ID],
    }


class ProductionRuntimeBoundariesTests(unittest.TestCase):
    """Verify that production adapters share only their run's evidence."""

    def test_retrieved_evidence_reaches_production_validation(self) -> None:
        """Evidence returned through the dispatcher should ground validation."""

        boundaries = build_production_runtime_boundaries(
            dispatcher=SyntheticEncounterDispatcher(),
            baseline_finding=make_baseline_finding(),
            as_of=date(2026, 11, 6),
            claim_context=make_claim_tool_context(),
        )

        boundaries.tool_dispatcher.dispatch(
            request_id="run-1:tool:1",
            tool_name=TOOL_NAME,
            tool_arguments={"claim_id": CLAIM_ID},
        )
        validation_result = boundaries.decision_validator.validate(
            make_decision_payload()
        )

        self.assertEqual(
            OfflineValidationStatus.ACCEPTED,
            validation_result.status,
        )
        self.assertEqual(
            (PASSAGE_ID,),
            tuple(
                passage.passage_id
                for passage in boundaries.evidence_store.evidence_passages
            ),
        )

    def test_separate_boundary_sets_do_not_share_evidence(self) -> None:
        """Evidence from one claim run must not leak into another run."""

        first_run = build_production_runtime_boundaries(
            dispatcher=SyntheticEncounterDispatcher(),
            baseline_finding=make_baseline_finding(),
            as_of=date(2026, 11, 6),
            claim_context=make_claim_tool_context(),
        )
        second_run = build_production_runtime_boundaries(
            dispatcher=SyntheticEncounterDispatcher(),
            baseline_finding=make_baseline_finding(),
            as_of=date(2026, 11, 6),
            claim_context=make_claim_tool_context(),
        )

        first_run.tool_dispatcher.dispatch(
            request_id="run-1:tool:1",
            tool_name=TOOL_NAME,
            tool_arguments={"claim_id": CLAIM_ID},
        )

        first_result = first_run.decision_validator.validate(make_decision_payload())
        second_result = second_run.decision_validator.validate(make_decision_payload())

        self.assertEqual(
            OfflineValidationStatus.ACCEPTED,
            first_result.status,
        )
        self.assertEqual(
            OfflineValidationStatus.MANUAL_REVIEW,
            second_result.status,
        )
        self.assertEqual(
            (),
            second_run.evidence_store.evidence_passages,
        )

    def test_claim_context_must_belong_to_baseline_claim(self) -> None:
        """Production boundaries must not combine two claim identities."""

        with self.assertRaisesRegex(ValueError, "claim_id mismatch"):
            build_production_runtime_boundaries(
                dispatcher=SyntheticEncounterDispatcher(),
                baseline_finding=make_baseline_finding(),
                claim_context=make_claim_tool_context(
                    claim_id="CLM-ANOTHER-PATIENT",
                ),
                as_of=date(2026, 11, 6),
            )


if __name__ == "__main__":
    unittest.main()
