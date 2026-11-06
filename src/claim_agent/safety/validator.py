from typing import List, Optional
from datetime import date
from claim_agent.contracts import (
    AgentDecision,
    ValidationResult,
    EvidencePassage,
    BaselineFinding,
    Disposition,
    ValidationProblem,
    ValidationOutcome,
    CriterionStatus,
    PROBLEM_PROHIBITED_BILLING, PROBLEM_UNAUTHORIZED_OVERRIDE, 
    PROBLEM_DEADLINE_EXPIRED, PROBLEM_FORMULA_NOT_SATISFIED, 
    PROBLEM_MISSING_CITATION, PROBLEM_UNRESOLVED_CITATION, PROBLEM_UNRESOLVED_CRITERION,
    ESCALATION_PROHIBITED_BILLING, ESCALATION_UNAUTHORIZED_OVERRIDE, 
    ESCALATION_DEADLINE_EXPIRED, ESCALATION_FORMULA_NOT_SATISFIED, ESCALATION_MISSING_EVIDENCE
)

def _evaluate_formula(formula_tokens: List[str], supported_criteria: set) -> bool:
    """Basic evaluator for policy boolean formulas like ['A', 'AND', 'B']."""
    if not formula_tokens:
        return True
        
    result = True
    current_op = "AND"
    
    for token in formula_tokens:
        if token in ("AND", "OR"):
            current_op = token
        else:
            is_supported = token in supported_criteria
            if current_op == "AND":
                result = result and is_supported
            elif current_op == "OR":
                result = result or is_supported
                
    return result

def validate_agent_decision(
    decision: AgentDecision,
    finding: BaselineFinding,
    retrieved_evidence: List[EvidencePassage],
    as_of: date,
    policy_formula: Optional[List[str]] = None
) -> ValidationResult:
    problems: List[ValidationProblem] = []
    manual_reasons: List[str] = []

    baseline_disposition = finding.baseline_disposition
    is_manual = decision.recommended_disposition == Disposition.MANUAL_REVIEW

    # 1. Patient Billing Restriction (Terminal)
    has_co_denial = any(code.startswith("CO-") for code in finding.denial_codes)
    if decision.recommended_disposition == Disposition.BILL_PATIENT and (baseline_disposition != Disposition.BILL_PATIENT or has_co_denial):
        problems.append(ValidationProblem(
            code=PROBLEM_PROHIBITED_BILLING, field_path="recommended_disposition",
            message="Agent attempted to bill patient for a CO denial or without baseline authorization.",
            is_repairable=False
        ))
        manual_reasons.append(ESCALATION_PROHIBITED_BILLING)

    # 2. Unauthorized Override (Terminal)
    if baseline_disposition == Disposition.MANUAL_REVIEW and not is_manual:
        problems.append(ValidationProblem(
            code=PROBLEM_UNAUTHORIZED_OVERRIDE, field_path="recommended_disposition",
            message="Agent cannot override a deterministic MANUAL_REVIEW.",
            is_repairable=False
        ))
        manual_reasons.append(ESCALATION_UNAUTHORIZED_OVERRIDE)

    # 3. Deadline Check (Terminal)
    if not is_manual and finding.filing_deadline and finding.filing_deadline < as_of:
        problems.append(ValidationProblem(
            code=PROBLEM_DEADLINE_EXPIRED, field_path="recommended_disposition",
            message="Agent recommended an action, but its filing deadline has already expired.",
            is_repairable=False
        ))
        manual_reasons.append(ESCALATION_DEADLINE_EXPIRED)

    # 4. Policy Formula Validation (Terminal)
    if decision.recommended_disposition == Disposition.APPEAL_WITH_DOCUMENTATION and policy_formula:
        supported = {c.criterion_id for c in decision.criterion_assessments if c.status == CriterionStatus.SUPPORTED}
        if not _evaluate_formula(policy_formula, supported):
            problems.append(ValidationProblem(
                code=PROBLEM_FORMULA_NOT_SATISFIED, field_path="criterion_assessments",
                message="The agent's supported criteria do not satisfy the required policy formula.",
                is_repairable=False
            ))
            manual_reasons.append(ESCALATION_FORMULA_NOT_SATISFIED)

    # 5. Missing Evidence / Empty Citations (Terminal if no evidence, Repairable otherwise)
    if not is_manual and not decision.citation_ids:
        if not retrieved_evidence:
            problems.append(ValidationProblem(
                code=PROBLEM_MISSING_CITATION, field_path="citation_ids",
                message="No evidence was retrieved, so the lack of citations is terminal.",
                is_repairable=False
            ))
            manual_reasons.append(ESCALATION_MISSING_EVIDENCE)
        else:
            problems.append(ValidationProblem(
                code=PROBLEM_MISSING_CITATION, field_path="citation_ids",
                message="A non-manual disposition requires at least one resolvable citation.",
                is_repairable=True
            ))

    # 6. Unresolved Citations (Repairable)
    valid_passage_ids = {passage.passage_id for passage in retrieved_evidence}
    for idx, citation in enumerate(decision.citation_ids):
        if citation not in valid_passage_ids:
            problems.append(ValidationProblem(
                code=PROBLEM_UNRESOLVED_CITATION, field_path=f"citation_ids[{idx}]",
                message=f"Citation '{citation}' not found in retrieved evidence.",
                is_repairable=True
            ))

    # 7. Criterion-Level Unresolved Citations & Support Enforcement (Repairable)
    for c_idx, criterion in enumerate(decision.criterion_assessments):
        if criterion.status == CriterionStatus.SUPPORTED and not criterion.citation_ids:
            problems.append(ValidationProblem(
                code=PROBLEM_MISSING_CITATION, field_path=f"criterion_assessments[{c_idx}].citation_ids",
                message=f"Criterion '{criterion.criterion_id}' is SUPPORTED but missing citations.",
                is_repairable=True
            ))
            
        for cit_idx, citation in enumerate(criterion.citation_ids):
            if citation not in valid_passage_ids:
                problems.append(ValidationProblem(
                    code=PROBLEM_UNRESOLVED_CRITERION, field_path=f"criterion_assessments[{c_idx}].citation_ids[{cit_idx}]",
                    message=f"Criterion citation '{citation}' not found.",
                    is_repairable=True
                ))

    # Determine explicit outcome
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
        contract_version="0.1",
        decision_id=decision.decision_id,
        claim_id=decision.claim_id,
        service_line_id=decision.service_line_id,
        outcome=outcome,
        approval_state="PENDING_REVIEW",
        validation_problems=problems,
        validated_decision=validated_decision,
        manual_review_reasons=manual_reasons
    )