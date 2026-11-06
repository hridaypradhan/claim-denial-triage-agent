"""Assemble production-shaped boundaries for one agent run.

The dispatcher adapter and decision validator must share the same evidence
store. This module creates that relationship in one place so callers cannot
accidentally validate against a different run's evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from claim_agent.agent.decision_validation_adapter import (
    DecisionValidationAdapter,
)
from claim_agent.agent.run_evidence import RunEvidenceStore
from claim_agent.agent.tool_result_adapter import (
    SharedToolDispatcher,
    ToolResultDispatcherAdapter,
)
from claim_agent.contracts import BaselineFinding


@dataclass(frozen=True)
class ProductionRuntimeBoundaries:
    """Production adapters and evidence state belonging to one run."""

    tool_dispatcher: ToolResultDispatcherAdapter
    decision_validator: DecisionValidationAdapter
    evidence_store: RunEvidenceStore


def build_production_runtime_boundaries(
    dispatcher: SharedToolDispatcher,
    baseline_finding: BaselineFinding,
    as_of: date,
) -> ProductionRuntimeBoundaries:
    """Create production adapters that share one run-scoped evidence store."""

    evidence_store = RunEvidenceStore()

    tool_dispatcher = ToolResultDispatcherAdapter(
        dispatcher=dispatcher,
        observation_consumer=evidence_store.record_observation,
    )
    decision_validator = DecisionValidationAdapter(
        baseline_finding=baseline_finding,
        evidence_provider=lambda: evidence_store.evidence_passages,
        policy_formula_provider=lambda: evidence_store.policy_formula,
        as_of=as_of,
    )

    return ProductionRuntimeBoundaries(
        tool_dispatcher=tool_dispatcher,
        decision_validator=decision_validator,
        evidence_store=evidence_store,
    )
