from typing import List
from claim_agent.contracts import (
    AgentDecision,
    ValidationResult,
    EvidencePassage,
    Disposition,
    ValidationProblem,
    ValidationOutcome
)

def validate_agent_decision(
    decision: AgentDecision,
    retrieved_evidence: List[EvidencePassage],
    baseline_disposition: Disposition
) -> ValidationResult:
    """
    Evaluates the LLM's AgentDecision against strict deterministic safety boundaries.
    Collects all applicable validation problems deterministically before determining the outcome.
    """
    problems: List[ValidationProblem] = []
    manual_reasons: List[str] = []

    # 1. Patient Billing Restriction (Terminal)
    # The agent cannot turn a provider-liability disposition into patient liability.
    if decision.recommended_disposition == Disposition.BILL_PATIENT and baseline_disposition != Disposition.BILL_PATIENT:
        problems.append(ValidationProblem(
            code="PROHIBITED_PATIENT_BILLING",
            field_path="recommended_disposition",
            message="Agent attempted to bill patient, but the deterministic baseline did not authorize it.",
            is_repairable=False
        ))
        manual_reasons.append("SAFETY_ESCALATION_PROHIBITED_BILLING")

    # 2. Unauthorized Override (Terminal)
    # The agent cannot override a deterministic MANUAL_REVIEW baseline finding.
    if baseline_disposition == Disposition.MANUAL_REVIEW and decision.recommended_disposition != Disposition.MANUAL_REVIEW:
        problems.append(ValidationProblem(
            code="UNAUTHORIZED_OVERRIDE",
            field_path="recommended_disposition",
            message="Agent cannot override a deterministic MANUAL_REVIEW baseline finding.",
            is_repairable=False
        ))
        manual_reasons.append("SAFETY_ESCALATION_UNAUTHORIZED_OVERRIDE")

    # 3. Missing Citations (Repairable)
    # An evidence-dependent recommendation must have at least one citation.
    if decision.recommended_disposition != Disposition.MANUAL_REVIEW and not decision.citation_ids:
        problems.append(ValidationProblem(
            code="MISSING_CITATION",
            field_path="citation_ids",
            message="A non-manual disposition requires at least one resolvable citation.",
            is_repairable=True
        ))

    # 4. Unresolved Citations (Repairable)
    # Rejects citation IDs that were not retrieved in this specific run.
    valid_passage_ids = {passage.passage_id for passage in retrieved_evidence}
    for idx, citation in enumerate(decision.citation_ids):
        if citation not in valid_passage_ids:
            problems.append(ValidationProblem(
                code="UNRESOLVED_CITATION",
                field_path=f"citation_ids[{idx}]",
                message=f"Citation ID '{citation}' was not retrieved in this run.",
                is_repairable=True
            ))

    # 5. Criterion-Level Unresolved Citations (Repairable)
    for c_idx, criterion in enumerate(decision.criterion_assessments):
        for cit_idx, citation in enumerate(criterion.citation_ids):
            if citation not in valid_passage_ids:
                problems.append(ValidationProblem(
                    code="UNRESOLVED_CRITERION_CITATION",
                    field_path=f"criterion_assessments[{c_idx}].citation_ids[{cit_idx}]",
                    message=f"Criterion citation ID '{citation}' was not retrieved in this run.",
                    is_repairable=True
                ))

    # Determine explicit three-way outcome
    if any(not p.is_repairable for p in problems):
        outcome = ValidationOutcome.REJECTED_MANUAL_REVIEW
        validated_decision = None
    elif any(p.is_repairable for p in problems):
        outcome = ValidationOutcome.REPAIRABLE
        validated_decision = None
    else:
        outcome = ValidationOutcome.ACCEPTED
        validated_decision = decision

    return ValidationResult(
        outcome=outcome,
        approval_state="PENDING_REVIEW",
        validation_problems=problems,
        validated_decision=validated_decision,
        manual_review_reasons=manual_reasons
    )
