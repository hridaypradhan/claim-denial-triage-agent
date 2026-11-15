from datetime import date
from pathlib import Path

from claim_agent.contracts.schema import (
    BaselineFinding,
    Disposition,
    EvidenceRequirementCategory,
    OutputPackage,
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

M2_OUTPUT_DIR = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "results"
    / "m2-pilot"
    / "set_a-scripted-agent"
    / "output_packages"
)


def _load_preserved_finding(case_id: str) -> BaselineFinding:
    pkg_path = M2_OUTPUT_DIR / f"{case_id}.json"
    pkg = OutputPackage.model_validate_json(pkg_path.read_text(encoding="utf-8"))
    return pkg.baseline_finding


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


def test_preserved_m2_610101_missing_ncci_lookup_escalates_to_manual_review():
    finding = _load_preserved_finding("CLM-2026-610101-L1")

    decision = evaluate_required_evidence_safety(finding, available_evidence=[])

    assert decision.requires_manual_review is True
    assert decision.recommended_outcome == RouteOutcome.MANUAL_REVIEW
    assert decision.reason_code == REASON_MISSING_DECISION_CRITICAL
    assert decision.missing_critical_evidence == ["ncci_edit_lookup"]


def test_preserved_m2_610107_missing_cob_evidence_escalates_to_manual_review():
    finding = _load_preserved_finding("CLM-2026-610107-L1")

    decision = evaluate_required_evidence_safety(
        finding, available_evidence=["primary_eob"]
    )

    assert decision.requires_manual_review is True
    assert decision.recommended_outcome == RouteOutcome.MANUAL_REVIEW
    assert decision.reason_code == REASON_MISSING_DECISION_CRITICAL
    assert decision.missing_critical_evidence == ["cob_confirmation"]


def test_preserved_m2_610104_updated_insurance_card_is_helpful_and_safe_to_route():
    finding = _load_preserved_finding("CLM-2026-610104-L1")
    assert finding.baseline_disposition == Disposition.ROUTE_TO_CORRECT_PAYER
    assert finding.required_evidence == ["updated_insurance_card"]

    decision = evaluate_required_evidence_safety(finding, available_evidence=[])

    assert decision.requires_manual_review is False
    assert decision.recommended_outcome is None
    assert decision.reason_code == REASON_NONESSENTIAL_ONLY
    assert decision.missing_critical_evidence == []
    assert decision.nonessential_evidence == ["updated_insurance_card"]
    assert decision.classified_requirements[0].category == (
        EvidenceRequirementCategory.HELPFUL_NONESSENTIAL
    )


def test_preserved_m2_005311_and_610115_correction_inputs_consistent_when_omitted_or_empty():
    finding_005311 = _load_preserved_finding("CLM-2026-005311-L1")
    finding_610115 = _load_preserved_finding("CLM-2026-610115-L1")
    assert finding_005311.required_evidence == ["rendering_provider_npi"]
    assert finding_610115.required_evidence == ["procedure_code"]

    for finding, expected_field in (
        (finding_005311, "rendering_provider_npi"),
        (finding_610115, "procedure_code"),
    ):
        dec_omitted = evaluate_required_evidence_safety(finding)
        dec_empty = evaluate_required_evidence_safety(finding, correction_inputs={})

        for dec in (dec_omitted, dec_empty):
            assert dec.requires_manual_review is False
            assert dec.recommended_outcome is None
            assert dec.reason_code == REASON_CORRECTION_INPUTS_ONLY
            assert dec.expected_correction_inputs == [expected_field]
            assert dec.supplied_correction_inputs == []
            assert dec.classified_requirements[0].supplied is False

    dec_supplied = evaluate_required_evidence_safety(
        finding_005311,
        correction_inputs={"rendering_provider_npi": "1234567890"},
    )
    assert dec_supplied.requires_manual_review is False
    assert dec_supplied.expected_correction_inputs == ["rendering_provider_npi"]
    assert dec_supplied.supplied_correction_inputs == ["rendering_provider_npi"]
    assert dec_supplied.classified_requirements[0].supplied is True


def test_evidence_supported_appeal_does_not_override_agent_routing():
    finding = _make_finding(
        claim_id="CLM-2026-005097",
        root_cause=RootCause.MEDICAL_NECESSITY,
        disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
        denial_codes=["CO-50"],
        required_evidence=["clinical_note"],
    )

    decision = evaluate_required_evidence_safety(
        finding, available_evidence=["clinical_note"]
    )

    assert decision.requires_manual_review is False
    assert decision.recommended_outcome is None
    assert decision.reason_code == REASON_DECISION_CRITICAL_SATISFIED


def test_existing_deterministic_cases_without_evidence_gaps_proceed():
    finding = _make_finding(
        root_cause=RootCause.CONTRACTUAL,
        disposition=Disposition.NO_ACTION_CONTRACTUAL,
        denial_codes=["CO-45"],
        required_evidence=[],
    )

    decision = evaluate_required_evidence_safety(finding)

    assert decision.requires_manual_review is False
    assert decision.recommended_outcome is None
    assert decision.reason_code == REASON_NO_REQUIRED_EVIDENCE