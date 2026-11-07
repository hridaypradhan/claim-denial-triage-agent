from datetime import date
from typing import List, Optional

from claim_agent.contracts import (
    AgentDecision,
    AgentStatus,
    BaselineFinding,
    CriterionStatus,
    Disposition,
    ESCALATION_DEADLINE_EXPIRED,
    ESCALATION_FORMULA_NOT_SATISFIED,
    ESCALATION_IDENTITY_MISMATCH,
    ESCALATION_MISSING_ENCOUNTER_CITATION,
    ESCALATION_MISSING_EVIDENCE,
    ESCALATION_MISSING_POLICY_FORMULA,
    ESCALATION_PROHIBITED_APPEAL,
    ESCALATION_PROHIBITED_BILLING,
    ESCALATION_STATUS_MISMATCH,
    ESCALATION_UNAUTHORIZED_DISPOSITION,
    ESCALATION_UNAUTHORIZED_OVERRIDE,
    ESCALATION_UNSUPPORTED_CRITERION,
    EvidencePassage,
    PROBLEM_DEADLINE_EXPIRED,
    PROBLEM_FORMULA_NOT_SATISFIED,
    PROBLEM_IDENTITY_MISMATCH,
    PROBLEM_MISSING_CITATION,
    PROBLEM_MISSING_ENCOUNTER_CITATION,
    PROBLEM_MISSING_POLICY_FORMULA,
    PROBLEM_PROHIBITED_APPEAL,
    PROBLEM_PROHIBITED_BILLING,
    PROBLEM_STATUS_MISMATCH,
    PROBLEM_UNAUTHORIZED_DISPOSITION,
    PROBLEM_UNAUTHORIZED_OVERRIDE,
    PROBLEM_UNRESOLVED_CITATION,
    PROBLEM_UNRESOLVED_CRITERION,
    PROBLEM_UNSUPPORTED_CRITERION,
    RootCause,
    ValidationOutcome,
    ValidationProblem,
    ValidationResult,
)
from claim_agent.safety.formula_evaluator import (
    evaluate_criteria_formula,
)


def validate_agent_decision(
    decision: AgentDecision,
    finding: BaselineFinding,
    retrieved_evidence: List[EvidencePassage],
    as_of: date,
    policy_formula: Optional[str] = None,
) -> ValidationResult:
    problems: List[ValidationProblem] = []
    manual_review_reasons: List[str] = []

    baseline_disposition = finding.baseline_disposition
    recommends_manual_review = (
        decision.recommended_disposition == Disposition.MANUAL_REVIEW
    )

    # 1. Identity Mismatch (Terminal)
    if (
        decision.claim_id != finding.claim_id
        or decision.service_line_id != finding.service_line_id
        or decision.root_cause != finding.root_cause
    ):
        problems.append(
            ValidationProblem(
                code=PROBLEM_IDENTITY_MISMATCH,
                field_path="claim_id",
                message="Decision identity or root cause mismatch.",
                is_repairable=False,
            )
        )
        manual_review_reasons.append(ESCALATION_IDENTITY_MISMATCH)

    # 2. Status / Disposition Match (Terminal)
    if (
        decision.agent_status == AgentStatus.MANUAL_REVIEW_REQUIRED
        and not recommends_manual_review
    ):
        problems.append(
            ValidationProblem(
                code=PROBLEM_STATUS_MISMATCH,
                field_path="recommended_disposition",
                message="Agent status demands manual review but disposition does not match.",
                is_repairable=False,
            )
        )
        manual_review_reasons.append(ESCALATION_STATUS_MISMATCH)
    elif decision.agent_status == AgentStatus.COMPLETED and recommends_manual_review:
        problems.append(
            ValidationProblem(
                code=PROBLEM_STATUS_MISMATCH,
                field_path="recommended_disposition",
                message="Agent status is COMPLETED but disposition is MANUAL_REVIEW.",
                is_repairable=False,
            )
        )
        manual_review_reasons.append(ESCALATION_STATUS_MISMATCH)

    # 3. Patient Billing Restriction (Terminal)
    has_provider_liability_denial = any(
        code.startswith("CO-") for code in finding.denial_codes
    )
    if decision.recommended_disposition == Disposition.BILL_PATIENT and (
        baseline_disposition != Disposition.BILL_PATIENT
        or has_provider_liability_denial
    ):
        problems.append(
            ValidationProblem(
                code=PROBLEM_PROHIBITED_BILLING,
                field_path="recommended_disposition",
                message="Prohibited patient billing.",
                is_repairable=False,
            )
        )
        manual_review_reasons.append(ESCALATION_PROHIBITED_BILLING)

    # 4. Unauthorized Override (Terminal)
    if (
        baseline_disposition == Disposition.MANUAL_REVIEW
        and not recommends_manual_review
    ):
        problems.append(
            ValidationProblem(
                code=PROBLEM_UNAUTHORIZED_OVERRIDE,
                field_path="recommended_disposition",
                message="Cannot override baseline manual review.",
                is_repairable=False,
            )
        )
        manual_review_reasons.append(ESCALATION_UNAUTHORIZED_OVERRIDE)

    # 5. Baseline Disallowed Disposition (Terminal)
    if (
        baseline_disposition == Disposition.WRITE_OFF
        and decision.recommended_disposition != Disposition.WRITE_OFF
    ):
        problems.append(
            ValidationProblem(
                code=PROBLEM_UNAUTHORIZED_DISPOSITION,
                field_path="recommended_disposition",
                message="Agent disposition prohibited by baseline.",
                is_repairable=False,
            )
        )
        manual_review_reasons.append(ESCALATION_UNAUTHORIZED_DISPOSITION)

    # 6. Contractual Appeal Prohibition (Terminal)
    if (
        decision.recommended_disposition == Disposition.APPEAL_WITH_DOCUMENTATION
        and finding.root_cause == RootCause.CONTRACTUAL
    ):
        problems.append(
            ValidationProblem(
                code=PROBLEM_PROHIBITED_APPEAL,
                field_path="recommended_disposition",
                message="Cannot appeal contractual adjustments.",
                is_repairable=False,
            )
        )
        manual_review_reasons.append(ESCALATION_PROHIBITED_APPEAL)

    # 7. Deadline Check (Terminal)
    if not recommends_manual_review and finding.deadline_status in ("EXPIRED", "NONE"):
        problems.append(
            ValidationProblem(
                code=PROBLEM_DEADLINE_EXPIRED,
                field_path="recommended_disposition",
                message="Deadline expired or missing.",
                is_repairable=False,
            )
        )
        manual_review_reasons.append(ESCALATION_DEADLINE_EXPIRED)

    # 8. Missing Evidence Collection (Terminal)
    if not recommends_manual_review and not retrieved_evidence:
        problems.append(
            ValidationProblem(
                code=PROBLEM_MISSING_CITATION,
                field_path="citation_ids",
                message="Empty retrieval collection is terminal.",
                is_repairable=False,
            )
        )
        manual_review_reasons.append(ESCALATION_MISSING_EVIDENCE)

    # 9. Policy Formula Checks (Terminal)
    if decision.recommended_disposition == Disposition.APPEAL_WITH_DOCUMENTATION:
        if not policy_formula:
            problems.append(
                ValidationProblem(
                    code=PROBLEM_MISSING_POLICY_FORMULA,
                    field_path="criterion_assessments",
                    message="Policy formula is missing.",
                    is_repairable=False,
                )
            )
            manual_review_reasons.append(ESCALATION_MISSING_POLICY_FORMULA)
        else:
            supported_criterion_ids = {
                criterion.criterion_id
                for criterion in decision.criterion_assessments
                if criterion.status == CriterionStatus.SUPPORTED
            }
            if not evaluate_criteria_formula(
                policy_formula,
                supported_criterion_ids,
            ):
                problems.append(
                    ValidationProblem(
                        code=PROBLEM_FORMULA_NOT_SATISFIED,
                        field_path="criterion_assessments",
                        message="Policy formula not met.",
                        is_repairable=False,
                    )
                )
                manual_review_reasons.append(ESCALATION_FORMULA_NOT_SATISFIED)

    # 10. UNKNOWN or CONFLICTING Criteria (Terminal)
    if not recommends_manual_review:
        has_unsupported_criterion = any(
            criterion.status in (CriterionStatus.UNKNOWN, CriterionStatus.CONFLICTING)
            for criterion in decision.criterion_assessments
        )
        if has_unsupported_criterion:
            problems.append(
                ValidationProblem(
                    code=PROBLEM_UNSUPPORTED_CRITERION,
                    field_path="criterion_assessments",
                    message="UNKNOWN or CONFLICTING criteria are terminal.",
                    is_repairable=False,
                )
            )
            manual_review_reasons.append(ESCALATION_UNSUPPORTED_CRITERION)

    # 11. Repairable Citation Rules
    encounter_passage_ids = {
        passage.passage_id
        for passage in retrieved_evidence
        if passage.source_type == "encounter_note"
    }
    valid_passage_ids = {passage.passage_id for passage in retrieved_evidence}

    if (
        not recommends_manual_review
        and not decision.citation_ids
        and retrieved_evidence
    ):
        problems.append(
            ValidationProblem(
                code=PROBLEM_MISSING_CITATION,
                field_path="citation_ids",
                message="Non-manual disposition requires citations.",
                is_repairable=True,
            )
        )

    for citation_index, citation_id in enumerate(decision.citation_ids):
        if citation_id not in valid_passage_ids:
            problems.append(
                ValidationProblem(
                    code=PROBLEM_UNRESOLVED_CITATION,
                    field_path=f"citation_ids[{citation_index}]",
                    message=f"Citation '{citation_id}' unresolved.",
                    is_repairable=True,
                )
            )

    for criterion_index, criterion in enumerate(decision.criterion_assessments):
        if criterion.status == CriterionStatus.SUPPORTED:
            if not criterion.citation_ids:
                problems.append(
                    ValidationProblem(
                        code=PROBLEM_MISSING_CITATION,
                        field_path=f"criterion_assessments[{criterion_index}].citation_ids",
                        message="SUPPORTED requires citations.",
                        is_repairable=True,
                    )
                )
            if not any(cit in encounter_passage_ids for cit in criterion.citation_ids):
                problems.append(
                    ValidationProblem(
                        code=PROBLEM_MISSING_ENCOUNTER_CITATION,
                        field_path=f"criterion_assessments[{criterion_index}].citation_ids",
                        message="SUPPORTED requires at least one encounter note.",
                        is_repairable=True,
                    )
                )

        for citation_index, citation_id in enumerate(criterion.citation_ids):
            if citation_id not in valid_passage_ids:
                problems.append(
                    ValidationProblem(
                        code=PROBLEM_UNRESOLVED_CRITERION,
                        field_path=f"criterion_assessments[{criterion_index}].citation_ids[{citation_index}]",
                        message="Criterion citation unresolved.",
                        is_repairable=True,
                    )
                )

    if any(not problem.is_repairable for problem in problems):
        outcome = ValidationOutcome.REJECTED_MANUAL_REVIEW
        validated_decision = None
    elif any(problem.is_repairable for problem in problems):
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
        manual_review_reasons=manual_review_reasons,
    )
