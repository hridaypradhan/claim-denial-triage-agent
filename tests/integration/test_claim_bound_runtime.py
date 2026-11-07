"""Verify that integrated runs reject evidence requested for another claim."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from typing import Mapping

from claim_agent.agent.integrated_runtime import run_integrated_case
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from claim_agent.agent.runtime_state import RuntimePhase
from claim_agent.agent.scripted_model import (
    ScriptedModelClient,
    ScriptedModelOutput,
)
from claim_agent.agent.tool_request_guard import ClaimToolContext
from claim_agent.contracts import (
    BaselineFinding,
    Disposition,
    RootCause,
    RouteDecision,
    RouteOutcome,
)
from claim_agent.tools.dispatcher import (
    ENCOUNTER_NOTE_TOOL,
    MEDICAL_POLICY_TOOL,
    ToolDispatcher,
)
from claim_agent.tools.knowledge_store import KnowledgeStore

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[2] / "knowledge"

CLAIM_ID = "CLM-2026-005097"
PAYER = "BCBS_AZ"
PROCEDURE_CODE = "70553"
SERVICE_DATE = date(2026, 6, 2)


def make_baseline_finding() -> BaselineFinding:
    """Create the deterministic finding for the protected claim."""

    return BaselineFinding(
        claim_id=CLAIM_ID,
        service_line_id=1,
        denial_codes=["CO-50"],
        root_cause=RootCause.MEDICAL_NECESSITY,
        baseline_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
        required_evidence=[
            "medical_policy",
            "encounter_note",
        ],
        rationale="Policy and encounter evidence require interpretation.",
        filing_deadline=date(2026, 12, 31),
        deadline_status="ACTIVE",
    )


def make_claim_context() -> ClaimToolContext:
    """Return the authoritative fields constraining retrieval."""

    return ClaimToolContext(
        claim_id=CLAIM_ID,
        payer=PAYER,
        procedure_code=PROCEDURE_CODE,
        service_date=SERVICE_DATE,
    )


def make_route_decision() -> RouteDecision:
    """Authorize the two evidence tools used by these safety tests."""

    return RouteDecision(
        claim_id=CLAIM_ID,
        service_line_id=1,
        outcome=RouteOutcome.AGENT_REQUIRED,
        reason_code="EVIDENCE_EVALUATION_REQUIRED",
        routing_reason="The claim requires evidence interpretation.",
        authorized_tools=[
            MEDICAL_POLICY_TOOL,
            ENCOUNTER_NOTE_TOOL,
        ],
    )


def make_model(
    tool_name: str,
    tool_arguments: Mapping[str, object],
) -> ScriptedModelClient:
    """Create a model that makes one deliberately mismatched tool request."""

    return ScriptedModelClient(
        [
            ScriptedModelOutput(
                response_text=json.dumps(
                    {
                        "action": "call_tool",
                        "tool_name": tool_name,
                        "tool_arguments": dict(tool_arguments),
                    }
                )
            )
        ]
    )


class ClaimBoundRuntimeTests(unittest.TestCase):
    """Ensure cross-claim retrieval stops before evidence is collected."""

    def _assert_request_requires_manual_review(
        self,
        tool_name: str,
        tool_arguments: Mapping[str, object],
    ) -> None:
        """Run one mismatched request and verify its safe terminal state."""

        result = run_integrated_case(
            run_id="claim-bound-safety-test",
            system_instructions=("Use only evidence belonging to the active claim."),
            model_client=make_model(tool_name, tool_arguments),
            dispatcher=ToolDispatcher(KnowledgeStore(KNOWLEDGE_ROOT)),
            baseline_finding=make_baseline_finding(),
            claim_context=make_claim_context(),
            route_decision=make_route_decision(),
            budget_limits=RuntimeBudgetLimits(
                max_model_calls=1,
                max_tool_calls=1,
                max_repair_attempts=0,
                max_total_steps=2,
                max_runtime_seconds=30.0,
            ),
            as_of=date(2026, 11, 6),
            package_version="test-v1",
            knowledge_snapshot_id="test-knowledge-snapshot",
        )

        runtime_result = result.runtime_result

        self.assertEqual(
            RuntimePhase.MANUAL_REVIEW,
            runtime_result.final_state.phase,
        )
        self.assertEqual(
            "tool_boundary_error",
            runtime_result.final_state.terminal_reason,
        )
        self.assertEqual(
            Disposition.MANUAL_REVIEW,
            result.output_package.final_disposition,
        )
        self.assertEqual((), result.retrieved_evidence)
        self.assertIsNone(result.proposed_decision)
        self.assertIsNone(result.validation_result)
        self.assertEqual(
            1,
            runtime_result.budget_usage.tool_calls_used,
        )
        self.assertIn(
            "TOOL_FAILED",
            [entry.event_name for entry in runtime_result.trace_entries],
        )

    def test_wrong_procedure_requires_manual_review(self) -> None:
        """A 70551 lookup must not supply policy for a 70553 claim."""

        self._assert_request_requires_manual_review(
            MEDICAL_POLICY_TOOL,
            {
                "payer": PAYER,
                "procedure_code": "70551",
                "service_date": SERVICE_DATE.isoformat(),
                "query": "brain MRI medical necessity",
            },
        )

    def test_wrong_payer_requires_manual_review(self) -> None:
        """An Aetna policy must not supply evidence for a BCBS claim."""

        self._assert_request_requires_manual_review(
            MEDICAL_POLICY_TOOL,
            {
                "payer": "AETNA",
                "procedure_code": PROCEDURE_CODE,
                "service_date": SERVICE_DATE.isoformat(),
                "query": "brain MRI medical necessity",
            },
        )

    def test_wrong_service_date_requires_manual_review(self) -> None:
        """A different service date must not select the claim's policy."""

        self._assert_request_requires_manual_review(
            MEDICAL_POLICY_TOOL,
            {
                "payer": PAYER,
                "procedure_code": PROCEDURE_CODE,
                "service_date": "2025-06-02",
                "query": "brain MRI medical necessity",
            },
        )

    def test_wrong_claim_encounter_requires_manual_review(self) -> None:
        """Another claim's encounter note must not enter this run."""

        self._assert_request_requires_manual_review(
            ENCOUNTER_NOTE_TOOL,
            {
                "claim_id": "CLM-ANOTHER-PATIENT",
                "document_id": "ENC-ANOTHER-PATIENT-PN",
            },
        )


if __name__ == "__main__":
    unittest.main()
