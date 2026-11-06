"""Shared builders for offline runtime test suites."""

from __future__ import annotations

from typing import Iterable

from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from claim_agent.contracts import (
    AgentDecision,
    AgentStatus,
    Disposition,
    RootCause,
)


def make_test_limits() -> RuntimeBudgetLimits:
    """Provide compact limits for deterministic runtime tests."""

    return RuntimeBudgetLimits(
        max_model_calls=2,
        max_tool_calls=2,
        max_repair_attempts=1,
        max_total_steps=3,
    )


def make_test_agent_decision_payload(
    *,
    decision_id: str = "decision-test-001",
    claim_id: str = "CLM-TEST-001",
    service_line_id: int = 1,
    root_cause: RootCause = RootCause.MEDICAL_NECESSITY,
    recommended_disposition: Disposition = Disposition.MANUAL_REVIEW,
    agent_status: AgentStatus = AgentStatus.MANUAL_REVIEW_REQUIRED,
    rationale: str = "The available evidence requires human review.",
    citation_ids: Iterable[str] = (),
    manual_review_reasons: Iterable[str] = (
        "Additional evidence requires human review.",
    ),
) -> dict[str, object]:
    """Build a complete decision payload accepted by contract version 0.1.

    Most runtime tests care about one behavior rather than every decision
    field. This builder supplies readable defaults while allowing each test to
    override only the values relevant to its scenario.
    """

    decision = AgentDecision(
        decision_id=decision_id,
        claim_id=claim_id,
        service_line_id=service_line_id,
        root_cause=root_cause,
        recommended_disposition=recommended_disposition,
        actions=[],
        criterion_assessments=[],
        evidence_gaps=[],
        agent_status=agent_status,
        manual_review_reasons=list(manual_review_reasons),
        rationale=rationale,
        citation_ids=list(citation_ids),
    )

    # JSON mode converts enum members into the same strings a model response
    # would contain, keeping the fixture representative of the runtime input.
    return decision.model_dump(mode="json")
