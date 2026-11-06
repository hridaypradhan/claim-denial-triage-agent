import re
from typing import List, Optional
from datetime import date
from claim_agent.contracts import (
    AgentDecision, ValidationResult, EvidencePassage, BaselineFinding, Disposition,
    RootCause, ValidationProblem, ValidationOutcome, CriterionStatus, AgentStatus,
    PROBLEM_PROHIBITED_BILLING, PROBLEM_UNAUTHORIZED_OVERRIDE, PROBLEM_DEADLINE_EXPIRED, 
    PROBLEM_FORMULA_NOT_SATISFIED, PROBLEM_MISSING_CITATION, PROBLEM_UNRESOLVED_CITATION, 
    PROBLEM_UNRESOLVED_CRITERION, PROBLEM_IDENTITY_MISMATCH, PROBLEM_STATUS_MISMATCH, 
    PROBLEM_UNAUTHORIZED_DISPOSITION, PROBLEM_PROHIBITED_APPEAL, PROBLEM_MISSING_POLICY_FORMULA, 
    PROBLEM_UNSUPPORTED_CRITERION, PROBLEM_MISSING_ENCOUNTER_CITATION,
    ESCALATION_PROHIBITED_BILLING, ESCALATION_UNAUTHORIZED_OVERRIDE, ESCALATION_DEADLINE_EXPIRED, 
    ESCALATION_FORMULA_NOT_SATISFIED, ESCALATION_MISSING_EVIDENCE, ESCALATION_IDENTITY_MISMATCH, 
    ESCALATION_STATUS_MISMATCH, ESCALATION_UNAUTHORIZED_DISPOSITION, ESCALATION_PROHIBITED_APPEAL, 
    ESCALATION_MISSING_POLICY_FORMULA, ESCALATION_UNSUPPORTED_CRITERION, ESCALATION_MISSING_ENCOUNTER_CITATION
)

def _evaluate_formula(formula: str, supported_criteria: set) -> bool:
    """Evaluates precedence-aware boolean string formulas like 'A AND B AND (C OR D)'."""
    if not formula:
        return False
        
    tokens = re.findall(r'\(|\)|AND|OR|[\w-]+', formula)
    py_expr = []
    for t in tokens:
        if t == 'AND':
            py_expr.append('and')
        elif t == 'OR':
            py_expr.append('or')
        elif t in ('(', ')'):
            py_expr.append(t)
        else:
            py_expr.append(str(t in supported_criteria))
            
    try:
        return eval(" ".join(py_expr))
    except Exception:
        return False

def validate_agent_decision(
    decision: AgentDecision,
    finding: BaselineFinding,
    retrieved_evidence: List[EvidencePassage],
    as_of: date,
    policy_formula: Optional[str] = None
) -> ValidationResult:
    problems: List[ValidationProblem] = []
    manual_reasons: List[str] = []

    baseline_disposition = finding.baseline_disposition
    is_manual = decision.recommended_disposition == Disposition.MANUAL_REVIEW

    # 1. Identity Mismatch (Terminal)
    if decision.claim_id != finding.claim_id or decision.service_line_id != finding.service_line_id:
        problems.append(ValidationProblem(code=PROBLEM_IDENTITY_MISMATCH, field_path="claim_id", message="Decision identity mismatch.", is_repairable=False))
        manual_reasons.append(ESCALATION_IDENTITY_MISMATCH)

    # 2. Status / Disposition Match (Terminal)
    if decision.agent_status == AgentStatus.MANUAL_REVIEW_REQUIRED and not is_manual:
        problems.append(ValidationProblem(code=PROBLEM_STATUS_MISMATCH, field_path="recommended_disposition", message="Agent status demands manual review but disposition does not match.", is_repairable=False))
        manual_reasons.append(ESCALATION_STATUS_MISMATCH)

    # 3. Patient Billing Restriction (Terminal)
    has_co_denial = any(code.startswith("CO-") for code in finding.denial_codes)
    if decision.recommended_disposition == Disposition.BILL_PATIENT and (baseline_disposition != Disposition.BILL_PATIENT or has_co_denial):
        problems.append(ValidationProblem(code=PROBLEM_PROHIBITED_BILLING, field_path="recommended_disposition", message="Prohibited patient billing.", is_repairable=False))
        manual_reasons.append(ESCALATION_PROHIBITED_BILLING)

    # 4. Unauthorized Override (Terminal)
    if baseline_disposition == Disposition.MANUAL_REVIEW and not is_manual:
        problems.append(ValidationProblem(code=PROBLEM_UNAUTHORIZED_OVERRIDE, field_path="recommended_disposition", message="Cannot override baseline manual review.", is_repairable=False))
        manual_reasons.append(ESCALATION_UNAUTHORIZED_OVERRIDE)

    # 5. Baseline Disallowed Disposition (Terminal)
    if baseline_disposition == Disposition.WRITE_OFF and decision.recommended_disposition != Disposition.WRITE_OFF:
        problems.append(ValidationProblem(code=PROBLEM_UNAUTHORIZED_DISPOSITION, field_path="recommended_disposition", message="Agent disposition prohibited by baseline.", is_repairable=False))
        manual_reasons.append(ESCALATION_UNAUTHORIZED_DISPOSITION)

    # 6. Contractual Appeal Prohibition (Terminal)
    if decision.recommended_disposition == Disposition.APPEAL_WITH_DOCUMENTATION and finding.root_cause == RootCause.CONTRACTUAL:
        problems.append(ValidationProblem(code=PROBLEM_PROHIBITED_APPEAL, field_path="recommended_disposition", message="Cannot appeal contractual adjustments.", is_repairable=False))
        manual_reasons.append(ESCALATION_PROHIBITED_APPEAL)

    # 7. Deadline Check (Terminal)
    if not is_manual and finding.deadline_status in ("EXPIRED", "NONE"):
        problems.append(ValidationProblem(code=PROBLEM_DEADLINE_EXPIRED, field_path="recommended_disposition", message="Deadline expired or missing.", is_repairable=False))
        manual_reasons.append(ESCALATION_DEADLINE_EXPIRED)

    # 8. Missing Evidence Collection (Terminal)
    if not is_manual and not retrieved_evidence:
        problems.append(ValidationProblem(code=PROBLEM_MISSING_CITATION, field_path="citation_ids", message="Empty retrieval collection is terminal.", is_repairable=False))
        manual_reasons.append(ESCALATION_MISSING_EVIDENCE)

    # 9. Policy Formula Checks (Terminal)
    if decision.recommended_disposition == Disposition.APPEAL_WITH_DOCUMENTATION:
        if not policy_formula:
            problems.append(ValidationProblem(code=PROBLEM_MISSING_POLICY_FORMULA, field_path="criterion_assessments", message="Policy formula is missing.", is_repairable=False))
            manual_reasons.append(ESCALATION_MISSING_POLICY_FORMULA)
        else:
            supported = {c.criterion_id for c in decision.criterion_assessments if c.status == CriterionStatus.SUPPORTED}
            if not _evaluate_formula(policy_formula, supported):
                problems.append(ValidationProblem(code=PROBLEM_FORMULA_NOT_SATISFIED, field_path="criterion_assessments", message="Policy formula not met.", is_repairable=False))
                manual_reasons.append(ESCALATION_FORMULA_NOT_SATISFIED)

    # 10. UNKNOWN or CONFLICTING Criteria (Terminal)
    if not is_manual:
        has_invalid = any(c.status in (CriterionStatus.UNKNOWN, CriterionStatus.CONFLICTING) for c in decision.criterion_assessments)
        if has_invalid:
            problems.append(ValidationProblem(code=PROBLEM_UNSUPPORTED_CRITERION, field_path="criterion_assessments", message="UNKNOWN or CONFLICTING criteria are terminal.", is_repairable=False))
            manual_reasons.append(ESCALATION_UNSUPPORTED_CRITERION)

    # 11. Repairable Citation Rules
    encounter_passage_ids = {p.passage_id for p in retrieved_evidence if p.source_type == "encounter_note"}
    valid_passage_ids = {p.passage_id for p in retrieved_evidence}
    
    if not is_manual and not decision.citation_ids and retrieved_evidence:
        problems.append(ValidationProblem(code=PROBLEM_MISSING_CITATION, field_path="citation_ids", message="Non-manual disposition requires citations.", is_repairable=True))
        
    for idx, citation in enumerate(decision.citation_ids):
        if citation not in valid_passage_ids:
            problems.append(ValidationProblem(code=PROBLEM_UNRESOLVED_CITATION, field_path=f"citation_ids[{idx}]", message=f"Citation '{citation}' unresolved.", is_repairable=True))

    for c_idx, criterion in enumerate(decision.criterion_assessments):
        if criterion.status == CriterionStatus.SUPPORTED:
            if not criterion.citation_ids:
                problems.append(ValidationProblem(code=PROBLEM_MISSING_CITATION, field_path=f"criterion_assessments[{c_idx}].citation_ids", message="SUPPORTED requires citations.", is_repairable=True))
            if not any(cit in encounter_passage_ids for cit in criterion.citation_ids):
                problems.append(ValidationProblem(code=PROBLEM_MISSING_ENCOUNTER_CITATION, field_path=f"criterion_assessments[{c_idx}].citation_ids", message="SUPPORTED requires at least one encounter note.", is_repairable=True))
                
        for cit_idx, citation in enumerate(criterion.citation_ids):
            if citation not in valid_passage_ids:
                problems.append(ValidationProblem(code=PROBLEM_UNRESOLVED_CRITERION, field_path=f"criterion_assessments[{c_idx}].citation_ids[{cit_idx}]", message="Criterion citation unresolved.", is_repairable=True))

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
        contract_version="0.1", decision_id=decision.decision_id,
        claim_id=decision.claim_id, service_line_id=decision.service_line_id,
        outcome=outcome, approval_state="PENDING_REVIEW",
        validation_problems=problems, validated_decision=validated_decision,
        manual_review_reasons=manual_reasons
    )