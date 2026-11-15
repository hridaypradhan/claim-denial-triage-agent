from datetime import date

from claim_agent.contracts.schema import (
    BaselineFinding,
    Disposition,
    EvidenceRequirementCategory,
    RootCause,
    RouteOutcome,
)
from claim_agent.safety.required_evidence import (
    REASON_CORRECTION_INPUTS_ONLY,
    REASON_DECISION_CRITICAL_SATISFIED,
    REASON_MISSING_DECISION_CRITICAL,
    REASON_NONESSENTIAL_ONLY,
    REASON_NO_REQUIRED_EVIDENCE,
    evaluate_required_evidence_safety,
)


def _make_finding(
    *,
    claim_id: str = "CLM-2026-TEST",
    root_cause: RootCause = RootCause.CODING,
    disposition: Disposition = Disposition.CORRECT_AND_RESUBMIT,
    denial_codes: list[str] | None = None,
    required_evidence: list[str] | None = None,
) -> BaselineFinding:
    return BaselineFinding(
        claim_id=claim_id,
        service_line_id=1,
        denial_codes=denial_codes or ["CO-16"],
        root_cause=root_cause,
        baseline_disposition=disposition,
        required_evidence=required_evidence or [],
        rationale="Test rationale.",
        filing_deadline=date(2027, 7, 1),
        deadline_status="ACTIVE",
    )


def test_milestone_2_case_610101_missing_ncci_lookup_requires_manual_review():
    finding = _make_finding(
        claim_id="CLM-2026-610101",
        root_cause=RootCause.BUNDLING_NCCI,
        disposition=Disposition.CORRECT_AND_RESUBMIT,
        denial_codes=["CO-97"],
        required_evidence=["ncci_edit_lookup"],
    )

    decision = evaluate_required_evidence_safety(finding, available_evidence=[])

    assert decision.requires_manual_review is True
    assert decision.recommended_outcome == RouteOutcome.MANUAL_REVIEW
    assert decision.reason_code == REASON_MISSING_DECISION_CRITICAL
    assert decision.missing_critical_evidence == ["ncci_edit_lookup"]


def test_milestone_2_case_610107_missing_cob_evidence_requires_manual_review():
    finding = _make_finding(
        claim_id="CLM-2026-610107",
        root_cause=RootCause.COORDINATION_OF_BENEFITS,
        disposition=Disposition.ROUTE_TO_CORRECT_PAYER,
        denial_codes=["CO-22", "MA04"],
        required_evidence=["primary_eob", "cob_confirmation"],
    )

    decision = evaluate_required_evidence_safety(
        finding, available_evidence=["primary_eob"]
    )

    assert decision.requires_manual_review is True
    assert decision.recommended_outcome == RouteOutcome.MANUAL_REVIEW
    assert decision.reason_code == REASON_MISSING_DECISION_CRITICAL
    assert decision.missing_critical_evidence == ["cob_confirmation"]


def test_correction_supplied_fields_do_not_force_manual_review():
    finding = _make_finding(
        root_cause=RootCause.CODING,
        disposition=Disposition.CORRECT_AND_RESUBMIT,
        denial_codes=["CO-16", "N290", "M76"],
        required_evidence=["rendering_provider_npi", "diagnosis_code"],
    )

    decision = evaluate_required_evidence_safety(
        finding,
        available_evidence=[],
        correction_inputs={
            "rendering_provider_npi": "1234567890",
            "diagnosis_code": "M54.5",
        },
    )

    assert decision.requires_manual_review is False
    assert decision.recommended_outcome == RouteOutcome.DETERMINISTIC_ONLY
    assert decision.reason_code == REASON_CORRECTION_INPUTS_ONLY
    assert decision.correction_supplied_inputs == [
        "rendering_provider_npi",
        "diagnosis_code",
    ]
    assert decision.missing_critical_evidence == []


def test_helpful_nonessential_documents_do_not_force_manual_review():
    finding = _make_finding(
        root_cause=RootCause.NON_COVERED_BENEFIT,
        disposition=Disposition.BILL_PATIENT,
        denial_codes=["PR-96", "N130"],
        required_evidence=["plan_benefit_reference"],
    )

    decision = evaluate_required_evidence_safety(finding, available_evidence=[])

    assert decision.requires_manual_review is False
    assert decision.recommended_outcome == RouteOutcome.DETERMINISTIC_ONLY
    assert decision.reason_code == REASON_NONESSENTIAL_ONLY
    assert decision.nonessential_evidence == ["plan_benefit_reference"]
    assert all(
        item.category == EvidenceRequirementCategory.HELPFUL_NONESSENTIAL
        for item in decision.classified_requirements
    )


def test_existing_deterministic_cases_without_evidence_gaps_proceed():
    finding = _make_finding(
        root_cause=RootCause.CONTRACTUAL,
        disposition=Disposition.NO_ACTION_CONTRACTUAL,
        denial_codes=["CO-45"],
        required_evidence=[],
    )

    decision = evaluate_required_evidence_safety(finding)

    assert decision.requires_manual_review is False
    assert decision.recommended_outcome == RouteOutcome.DETERMINISTIC_ONLY
    assert decision.reason_code == REASON_NO_REQUIRED_EVIDENCE


def test_decision_critical_evidence_satisfied_allows_proceeding():
    finding = _make_finding(
        claim_id="CLM-2026-610101",
        root_cause=RootCause.BUNDLING_NCCI,
        disposition=Disposition.CORRECT_AND_RESUBMIT,
        denial_codes=["CO-97"],
        required_evidence=["ncci_edit_lookup"],
    )

    decision = evaluate_required_evidence_safety(
        finding, available_evidence=["ncci_edit_lookup"]
    )

    assert decision.requires_manual_review is False
    assert decision.recommended_outcome == RouteOutcome.DETERMINISTIC_ONLY
    assert decision.reason_code == REASON_DECISION_CRITICAL_SATISFIED