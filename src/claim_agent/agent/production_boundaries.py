"""Assemble production-shaped boundaries for one agent run.

The dispatcher adapter and decision validator must share the same evidence
store. This module creates that relationship in one place so callers cannot
accidentally validate against a different run's evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from claim_agent.agent.decision_validation_adapter import (
    DecisionValidationAdapter,
)
from claim_agent.agent.run_evidence import RunEvidenceStore
from claim_agent.agent.tool_request_guard import (
    ClaimBoundToolDispatcher,
    ClaimToolContext,
)
from claim_agent.agent.tool_result_adapter import (
    SharedToolDispatcher,
    ToolResultDispatcherAdapter,
)
from claim_agent.contracts import BaselineFinding
from claim_agent.tools.deadline_tool import DeadlineToolDispatcher
from claim_agent.tools.eligibility import EligibilityToolDispatcher
from claim_agent.tools.knowledge_store import KnowledgeStore


@dataclass(frozen=True)
class ProductionRuntimeBoundaries:
    """Production adapters and evidence state belonging to one run."""

    tool_dispatcher: ToolResultDispatcherAdapter
    decision_validator: DecisionValidationAdapter
    evidence_store: RunEvidenceStore


def build_production_runtime_boundaries(
    dispatcher: SharedToolDispatcher,
    baseline_finding: BaselineFinding,
    claim_context: ClaimToolContext,
    as_of: date,
    eligibility_store: KnowledgeStore | None = None,
    required_evidence_source_ids: Sequence[str] = (),
) -> ProductionRuntimeBoundaries:
    """Create guarded production adapters sharing one run's evidence store."""

    if baseline_finding.claim_id != claim_context.claim_id:
        raise ValueError(
            "claim_id mismatch between BaselineFinding and ClaimToolContext"
        )

    evidence_store = RunEvidenceStore()

    # The model may suggest retrieval arguments, but this wrapper ensures that
    # payer, procedure, service date, and claim ID belong to the active claim.
    claim_bound_dispatcher = ClaimBoundToolDispatcher(
        dispatcher=dispatcher,
        claim_context=claim_context,
    )

    # calculate_deadline answers from this run's trusted finding and never
    # reaches the knowledge tools. The runtime still lets the model call it
    # only when the route authorizes it.
    deadline_dispatcher = DeadlineToolDispatcher(
        dispatcher=claim_bound_dispatcher,
        finding=baseline_finding,
        as_of=as_of,
    )

    active_dispatcher: SharedToolDispatcher = deadline_dispatcher

    if eligibility_store is not None:
        if claim_context.patient_account is None:
            raise ValueError(
                "Eligibility retrieval requires the active claim's "
                "patient_account."
            )

        active_dispatcher = EligibilityToolDispatcher(
            dispatcher=deadline_dispatcher,
            store=eligibility_store,
            patient_account=claim_context.patient_account,
            allowed_payers=claim_context.eligibility_payers,
        )

    tool_dispatcher = ToolResultDispatcherAdapter(
        dispatcher=active_dispatcher,
        observation_consumer=evidence_store.record_observation,
    )
    decision_validator = DecisionValidationAdapter(
        baseline_finding=baseline_finding,
        evidence_provider=lambda: evidence_store.evidence_passages,
        policy_formula_provider=lambda: evidence_store.policy_formula,
        as_of=as_of,
        required_evidence_source_ids=required_evidence_source_ids,
    )

    return ProductionRuntimeBoundaries(
        tool_dispatcher=tool_dispatcher,
        decision_validator=decision_validator,
        evidence_store=evidence_store,
    )
