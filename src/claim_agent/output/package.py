"""Assemble the final, always-pending package presented for human review."""

from typing import List, Optional

from claim_agent.contracts import (
    AgentDecision,
    BaselineFinding,
    CONTRACT_VERSION,
    Disposition,
    OutputPackage,
    RouteOutcome,
    ValidationOutcome,
    ValidationResult,
)


def create_output_package(
    run_id: str,
    package_version: str,
    knowledge_snapshot_id: str,
    finding: BaselineFinding,
    route_outcome: RouteOutcome,
    proposed_decision: Optional[AgentDecision] = None,
    validation_result: Optional[ValidationResult] = None,
    agent_terminal_reason: Optional[str] = None,
) -> OutputPackage:
    """Settle the safe disposition and consolidate human-review reasons."""

    # A deterministic route may keep its baseline disposition. An agent route
    # may keep only a decision that the deterministic validator accepted.
    # Every other path fails closed to manual review.
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

    # Collect reasons from each boundary so the reviewer sees one ordered list.
    reasons: List[str] = []
    if finding.deadline_status == "EXPIRED":
        reasons.append("DEADLINE_EXPIRED")
    if validation_result and validation_result.manual_review_reasons:
        reasons.extend(validation_result.manual_review_reasons)
    if (
        agent_terminal_reason
        and final_disposition == Disposition.MANUAL_REVIEW
    ):
        reasons.append(agent_terminal_reason)

    # dict preserves insertion order and removes repeated boundary reasons.
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
        manual_review_reasons=consolidated_reasons,
    )
