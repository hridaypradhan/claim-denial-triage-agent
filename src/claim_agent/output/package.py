from typing import List, Optional
from claim_agent.contracts import (
    OutputPackage, RouteOutcome, BaselineFinding, AgentDecision,
    ValidationResult, ValidationOutcome, Disposition, RootCause, CONTRACT_VERSION
)


def create_output_package(
    run_id: str,
    package_version: str,
    knowledge_snapshot_id: str,
    finding: BaselineFinding,
    route_outcome: RouteOutcome,
    proposed_decision: Optional[AgentDecision] = None,
    validation_result: Optional[ValidationResult] = None,
    agent_terminal_reason: Optional[str] = None
) -> OutputPackage:
    """
    Constructs and validates the OutputPackage, computing final disposition
    and consolidating manual review reasons.
    """
    # 1. Compute final disposition & root cause per Sachin's evaluation rules
    if route_outcome == RouteOutcome.DETERMINISTIC_ONLY:
        final_disposition = finding.baseline_disposition
    elif route_outcome == RouteOutcome.MANUAL_REVIEW:
        final_disposition = Disposition.MANUAL_REVIEW
    elif (
        route_outcome == RouteOutcome.AGENT_REQUIRED
        and validation_result is not None
        and validation_result.outcome == ValidationOutcome.ACCEPTED
        and validation_result.validated_decision is not None
    ):
        final_disposition = validation_result.validated_decision.recommended_disposition
    else:
        final_disposition = Disposition.MANUAL_REVIEW

    final_root_cause = finding.root_cause

    # 2. Consolidate manual_review_reasons at package level
    reasons: List[str] = []
    if finding.deadline_status == "EXPIRED":
        reasons.append("DEADLINE_EXPIRED")
    if validation_result and validation_result.manual_review_reasons:
        reasons.extend(validation_result.manual_review_reasons)
    if agent_terminal_reason:
        reasons.append(agent_terminal_reason)

    # Deduplicate while preserving order
    consolidated_reasons = list(dict.fromkeys(reasons))

    return OutputPackage(
        contract_version=CONTRACT_VERSION,
        run_id=run_id,
        package_version=package_version,
        knowledge_snapshot_id=knowledge_snapshot_id,
        claim_id=finding.claim_id,
        service_line_id=finding.service_line_id,
        route_outcome=route_outcome,
        baseline_finding=finding,
        proposed_decision=proposed_decision,
        validation_result=validation_result,
        agent_terminal_reason=agent_terminal_reason,
        final_disposition=final_disposition,
        final_root_cause=final_root_cause,
        manual_review_reasons=consolidated_reasons
    )