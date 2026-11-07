"""Run one routed claim through production-shaped runtime boundaries.

This module is the assembly point between the deterministic router and the
agent runtime. It validates that the agent is authorized before constructing
model, tool, evidence, and decision-validation boundaries.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional, Tuple

from claim_agent.agent.model_interface import ModelClient
from claim_agent.agent.offline_runtime import (
    OfflineAgentRuntime,
    OfflineRuntimeResult,
)
from claim_agent.agent.production_boundaries import (
    build_production_runtime_boundaries,
)
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from claim_agent.agent.runtime_case import prepare_runtime_case
from claim_agent.agent.tool_request_guard import ClaimToolContext
from claim_agent.agent.tool_result_adapter import SharedToolDispatcher
from claim_agent.contracts import (
    AgentDecision,
    BaselineFinding,
    EvidencePassage,
    OutputPackage,
    RouteDecision,
    ValidationResult,
)
from claim_agent.output.package import create_output_package


@dataclass(frozen=True)
class IntegratedCaseResult:
    """Observable runtime result and evidence collected during one case."""

    runtime_result: OfflineRuntimeResult
    retrieved_evidence: Tuple[EvidencePassage, ...]
    policy_formula: Optional[str]
    proposed_decision: Optional[AgentDecision]
    validation_result: Optional[ValidationResult]
    output_package: OutputPackage


def run_integrated_case(
    run_id: str,
    system_instructions: str,
    model_client: ModelClient,
    dispatcher: SharedToolDispatcher,
    baseline_finding: BaselineFinding,
    claim_context: ClaimToolContext,
    route_decision: RouteDecision,
    budget_limits: RuntimeBudgetLimits,
    as_of: date,
    package_version: str,
    knowledge_snapshot_id: str,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> IntegratedCaseResult:
    """Validate, assemble, and execute one authorized agent case."""

    # Route validation happens before any model or tool boundary is invoked.
    # This is like checking that a claim belongs in the specialist-review
    # queue before handing it to the specialist.
    prepared_case = prepare_runtime_case(
        baseline_finding=baseline_finding,
        route_decision=route_decision,
    )

    boundaries = build_production_runtime_boundaries(
        dispatcher=dispatcher,
        baseline_finding=baseline_finding,
        claim_context=claim_context,
        as_of=as_of,
    )

    runtime = OfflineAgentRuntime(
        model_client=model_client,
        tool_dispatcher=boundaries.tool_dispatcher,
        decision_validator=boundaries.decision_validator,
        budget_limits=budget_limits,
        allowed_tool_names=prepared_case.allowed_tool_names,
        monotonic_clock=monotonic_clock,
    )

    runtime_result = runtime.run(
        run_id=run_id,
        system_instructions=system_instructions,
        task_context=prepared_case.task_context,
    )

    output_package = create_output_package(
        run_id=run_id,
        package_version=package_version,
        knowledge_snapshot_id=knowledge_snapshot_id,
        finding=baseline_finding,
        route_outcome=route_decision.outcome,
        proposed_decision=(boundaries.decision_validator.last_proposed_decision),
        validation_result=(boundaries.decision_validator.last_validation_result),
        agent_terminal_reason=runtime_result.final_state.terminal_reason,
    )

    return IntegratedCaseResult(
        runtime_result=runtime_result,
        retrieved_evidence=boundaries.evidence_store.evidence_passages,
        policy_formula=boundaries.evidence_store.policy_formula,
        proposed_decision=(boundaries.decision_validator.last_proposed_decision),
        validation_result=(boundaries.decision_validator.last_validation_result),
        output_package=output_package,
    )
