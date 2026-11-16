import unittest
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from claim_agent.contracts.schema import (
    BaselineFinding,
    Disposition,
    EvidencePassage,
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


class TestRequiredEvidenceSafety(unittest.TestCase):
    def test_evidence_passage_remains_frozen_against_mutation(self):
        passage = EvidencePassage(
            passage_id="POL-UHC-72148@v1#p1",
            source_id="POL-UHC-72148",
            source_type="medical_policy",
            version="1",
            text="Original policy passage text.",
        )
        with self.assertRaises(ValidationError):
            passage.text = "mutated"  # type: ignore[misc]

    def test_preserved_m2_610101_missing_ncci_lookup_escalates_to_manual_review(self):
        finding = _load_preserved_finding("CLM-2026-610101-L1")

        decision = evaluate_required_evidence_safety(finding, available_evidence=[])

        self.assertTrue(decision.requires_manual_review)
        self.assertEqual(decision.recommended_outcome, RouteOutcome.MANUAL_REVIEW)
        self.assertEqual(decision.reason_code, REASON_MISSING_DECISION_CRITICAL)
        self.assertEqual(decision.missing_critical_evidence, ["ncci_edit_lookup"])

    def test_preserved_m2_610107_missing_cob_evidence_escalates_to_manual_review(self):
        finding = _load_preserved_finding("CLM-2026-610107-L1")

        decision = evaluate_required_evidence_safety(
            finding, available_evidence=["primary_eob"]
        )

        self.assertTrue(decision.requires_manual_review)
        self.assertEqual(decision.recommended_outcome, RouteOutcome.MANUAL_REVIEW)
        self.assertEqual(decision.reason_code, REASON_MISSING_DECISION_CRITICAL)
        self.assertEqual(decision.missing_critical_evidence, ["cob_confirmation"])

    def test_preserved_m2_610104_updated_insurance_card_is_helpful_and_safe_to_route(self):
        finding = _load_preserved_finding("CLM-2026-610104-L1")
        self.assertEqual(
            finding.baseline_disposition, Disposition.ROUTE_TO_CORRECT_PAYER
        )
        self.assertEqual(finding.required_evidence, ["updated_insurance_card"])

        decision = evaluate_required_evidence_safety(finding, available_evidence=[])

        self.assertFalse(decision.requires_manual_review)
        self.assertIsNone(decision.recommended_outcome)
        self.assertEqual(decision.reason_code, REASON_NONESSENTIAL_ONLY)
        self.assertEqual(decision.missing_critical_evidence, [])
        self.assertEqual(decision.nonessential_evidence, ["updated_insurance_card"])
        self.assertEqual(
            decision.classified_requirements[0].category,
            EvidenceRequirementCategory.HELPFUL_NONESSENTIAL,
        )

    def test_preserved_m2_005311_and_610115_correction_inputs_consistent_when_omitted_or_empty(self):
        finding_005311 = _load_preserved_finding("CLM-2026-005311-L1")
        finding_610115 = _load_preserved_finding("CLM-2026-610115-L1")
        self.assertEqual(finding_005311.required_evidence, ["rendering_provider_npi"])
        self.assertEqual(finding_610115.required_evidence, ["procedure_code"])

        for finding, expected_field in (
            (finding_005311, "rendering_provider_npi"),
            (finding_610115, "procedure_code"),
        ):
            dec_omitted = evaluate_required_evidence_safety(finding)
            dec_empty = evaluate_required_evidence_safety(finding, correction_inputs={})

            for dec in (dec_omitted, dec_empty):
                self.assertFalse(dec.requires_manual_review)
                self.assertIsNone(dec.recommended_outcome)
                self.assertEqual(dec.reason_code, REASON_CORRECTION_INPUTS_ONLY)
                self.assertEqual(dec.expected_correction_inputs, [expected_field])
                self.assertEqual(dec.supplied_correction_inputs, [])
                self.assertFalse(dec.classified_requirements[0].supplied)

        dec_supplied = evaluate_required_evidence_safety(
            finding_005311,
            correction_inputs={"rendering_provider_npi": "1234567890"},
        )
        self.assertFalse(dec_supplied.requires_manual_review)
        self.assertEqual(
            dec_supplied.expected_correction_inputs, ["rendering_provider_npi"]
        )
        self.assertEqual(
            dec_supplied.supplied_correction_inputs, ["rendering_provider_npi"]
        )
        self.assertTrue(dec_supplied.classified_requirements[0].supplied)

    def test_evidence_supported_appeal_does_not_override_agent_routing(self):
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

        self.assertFalse(decision.requires_manual_review)
        self.assertIsNone(decision.recommended_outcome)
        self.assertEqual(decision.reason_code, REASON_DECISION_CRITICAL_SATISFIED)

    def test_existing_deterministic_cases_without_evidence_gaps_proceed(self):
        finding = _make_finding(
            root_cause=RootCause.CONTRACTUAL,
            disposition=Disposition.NO_ACTION_CONTRACTUAL,
            denial_codes=["CO-45"],
            required_evidence=[],
        )

        decision = evaluate_required_evidence_safety(finding)

        self.assertFalse(decision.requires_manual_review)
        self.assertIsNone(decision.recommended_outcome)
        self.assertEqual(decision.reason_code, REASON_NO_REQUIRED_EVIDENCE)


if __name__ == "__main__":
    unittest.main()