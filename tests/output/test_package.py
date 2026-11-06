import unittest
from claim_agent.contracts import (
    BaselineFinding, AgentDecision, ValidationResult,
    Disposition, RootCause, RouteOutcome, AgentStatus, ValidationOutcome,
    OutputPackage,
)
from claim_agent.output.package import create_output_package


class TestOutputPackage(unittest.TestCase):

    def setUp(self):
        self.finding = BaselineFinding(
            claim_id="CLM-100", service_line_id=1, denial_codes=["CO-45"],
            root_cause=RootCause.MEDICAL_NECESSITY,
            baseline_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
            rationale="Test finding", deadline_status="ACTIVE"
        )

    # --- helpers ---
    def make_decision(self, decision_id="dec-1", claim_id="CLM-100", service_line_id=1,
                      disposition=Disposition.CORRECT_AND_RESUBMIT):
        return AgentDecision(
            decision_id=decision_id, claim_id=claim_id, service_line_id=service_line_id,
            root_cause=RootCause.MEDICAL_NECESSITY, recommended_disposition=disposition,
            agent_status=AgentStatus.COMPLETED, rationale="Agent rationale"
        )

    def make_accepted(self, decision_id="dec-1", claim_id="CLM-100", service_line_id=1,
                      disposition=Disposition.CORRECT_AND_RESUBMIT):
        decision = self.make_decision(decision_id, claim_id, service_line_id, disposition)
        return ValidationResult(
            decision_id=decision_id, claim_id=claim_id, service_line_id=service_line_id,
            outcome=ValidationOutcome.ACCEPTED, validated_decision=decision
        )

    def make_rejected(self, decision_id="dec-1", claim_id="CLM-100", service_line_id=1):
        return ValidationResult(
            decision_id=decision_id, claim_id=claim_id, service_line_id=service_line_id,
            outcome=ValidationOutcome.REJECTED_MANUAL_REVIEW,
            manual_review_reasons=["SAFETY_ESCALATION_MISSING_EVIDENCE"]
        )

    def make_repairable(self, decision_id="dec-1", claim_id="CLM-100", service_line_id=1):
        return ValidationResult(
            decision_id=decision_id, claim_id=claim_id, service_line_id=service_line_id,
            outcome=ValidationOutcome.REPAIRABLE
        )

    def direct_package(self, **overrides):
        """Builds OutputPackage directly, bypassing create_output_package."""
        fields = dict(
            run_id="run-1", package_version="v1", knowledge_snapshot_id="snap-1",
            claim_id="CLM-100", service_line_id=1,
            route_outcome=RouteOutcome.DETERMINISTIC_ONLY,
            baseline_finding=self.finding,
            final_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
            final_root_cause=RootCause.MEDICAL_NECESSITY,
        )
        fields.update(overrides)
        return OutputPackage(**fields)

    def direct_agent_package(self, **overrides):
        fields = dict(
            route_outcome=RouteOutcome.AGENT_REQUIRED,
            proposed_decision=self.make_decision(),
            validation_result=self.make_accepted(),
            final_disposition=Disposition.CORRECT_AND_RESUBMIT,
        )
        fields.update(overrides)
        return self.direct_package(**fields)

    # --- original tests ---
    def test_deterministic_only_package(self):
        pkg = create_output_package(
            run_id="run-1", package_version="v1", knowledge_snapshot_id="snap-1",
            finding=self.finding, route_outcome=RouteOutcome.DETERMINISTIC_ONLY
        )
        self.assertEqual(pkg.final_disposition, Disposition.APPEAL_WITH_DOCUMENTATION)
        self.assertEqual(pkg.final_root_cause, RootCause.MEDICAL_NECESSITY)
        self.assertEqual(pkg.run_id, "run-1")

    def test_agent_required_missing_terminal_reason_fails(self):
        with self.assertRaises(ValueError):
            create_output_package(
                run_id="run-1", package_version="v1", knowledge_snapshot_id="snap-1",
                finding=self.finding, route_outcome=RouteOutcome.AGENT_REQUIRED,
                proposed_decision=None, validation_result=None, agent_terminal_reason=None
            )

    def test_agent_required_with_terminal_reason(self):
        pkg = create_output_package(
            run_id="run-1", package_version="v1", knowledge_snapshot_id="snap-1",
            finding=self.finding, route_outcome=RouteOutcome.AGENT_REQUIRED,
            proposed_decision=None, validation_result=None,
            agent_terminal_reason="MODEL_CALL_BUDGET_EXHAUSTED"
        )
        self.assertEqual(pkg.agent_terminal_reason, "MODEL_CALL_BUDGET_EXHAUSTED")
        self.assertEqual(pkg.final_disposition, Disposition.MANUAL_REVIEW)
        self.assertIn("MODEL_CALL_BUDGET_EXHAUSTED", pkg.manual_review_reasons)

    # --- accepted / non-accepted agent packages ---
    def test_accepted_agent_package_succeeds(self):
        pkg = create_output_package(
            run_id="run-1", package_version="v1", knowledge_snapshot_id="snap-1",
            finding=self.finding, route_outcome=RouteOutcome.AGENT_REQUIRED,
            proposed_decision=self.make_decision(), validation_result=self.make_accepted()
        )
        self.assertEqual(pkg.final_disposition, Disposition.CORRECT_AND_RESUBMIT)
        self.assertEqual(pkg.final_root_cause, RootCause.MEDICAL_NECESSITY)

    def test_success_terminal_reason_is_not_manual_review_reason(self):
        """A completed run must not be mislabeled as requiring manual review."""

        pkg = create_output_package(
            run_id="run-1",
            package_version="v1",
            knowledge_snapshot_id="snap-1",
            finding=self.finding,
            route_outcome=RouteOutcome.AGENT_REQUIRED,
            proposed_decision=self.make_decision(),
            validation_result=self.make_accepted(),
            agent_terminal_reason="decision_accepted",
        )

        self.assertEqual("decision_accepted", pkg.agent_terminal_reason)
        self.assertEqual([], pkg.manual_review_reasons)

    def test_accepted_agent_package_direct_construction_succeeds(self):
        pkg = self.direct_agent_package()
        self.assertEqual(pkg.final_disposition, Disposition.CORRECT_AND_RESUBMIT)

    def test_rejected_agent_package_resolves_to_manual_review(self):
        pkg = create_output_package(
            run_id="run-1", package_version="v1", knowledge_snapshot_id="snap-1",
            finding=self.finding, route_outcome=RouteOutcome.AGENT_REQUIRED,
            proposed_decision=self.make_decision(), validation_result=self.make_rejected()
        )
        self.assertEqual(pkg.final_disposition, Disposition.MANUAL_REVIEW)
        self.assertIn("SAFETY_ESCALATION_MISSING_EVIDENCE", pkg.manual_review_reasons)

    def test_repairable_agent_package_resolves_to_manual_review(self):
        pkg = self.direct_agent_package(
            validation_result=self.make_repairable(),
            final_disposition=Disposition.MANUAL_REVIEW,
        )
        self.assertEqual(pkg.final_disposition, Disposition.MANUAL_REVIEW)

    def test_proposed_decision_without_validation_result_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_agent_package(
                validation_result=None, final_disposition=Disposition.MANUAL_REVIEW
            )

    def test_validation_result_without_proposed_decision_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_agent_package(
                proposed_decision=None, final_disposition=Disposition.CORRECT_AND_RESUBMIT
            )

    # --- route outcomes that forbid agent artifacts ---
    def test_deterministic_only_rejects_proposed_decision(self):
        with self.assertRaises(ValueError):
            self.direct_package(
                route_outcome=RouteOutcome.DETERMINISTIC_ONLY,
                proposed_decision=self.make_decision(),
                validation_result=self.make_rejected(),
            )

    def test_deterministic_only_rejects_validation_result(self):
        with self.assertRaises(ValueError):
            self.direct_package(
                route_outcome=RouteOutcome.DETERMINISTIC_ONLY,
                validation_result=self.make_rejected(),
            )

    def test_manual_review_rejects_proposed_decision(self):
        with self.assertRaises(ValueError):
            self.direct_package(
                route_outcome=RouteOutcome.MANUAL_REVIEW,
                proposed_decision=self.make_decision(),
                validation_result=self.make_rejected(),
                final_disposition=Disposition.MANUAL_REVIEW,
            )

    def test_manual_review_rejects_validation_result(self):
        with self.assertRaises(ValueError):
            self.direct_package(
                route_outcome=RouteOutcome.MANUAL_REVIEW,
                validation_result=self.make_rejected(),
                final_disposition=Disposition.MANUAL_REVIEW,
            )

    def test_manual_review_route_package_succeeds(self):
        pkg = create_output_package(
            run_id="run-1", package_version="v1", knowledge_snapshot_id="snap-1",
            finding=self.finding, route_outcome=RouteOutcome.MANUAL_REVIEW
        )
        self.assertEqual(pkg.final_disposition, Disposition.MANUAL_REVIEW)

    # --- identity mismatches ---
    def test_package_claim_id_mismatch_with_baseline_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_package(claim_id="CLM-999")

    def test_package_service_line_id_mismatch_with_baseline_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_package(service_line_id=2)

    def test_proposed_decision_claim_id_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_agent_package(
                proposed_decision=self.make_decision(claim_id="CLM-999"),
            )

    def test_proposed_decision_service_line_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_agent_package(
                proposed_decision=self.make_decision(service_line_id=2),
            )

    def test_validation_result_claim_id_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_agent_package(
                validation_result=self.make_accepted(claim_id="CLM-999"),
            )

    def test_validation_result_service_line_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_agent_package(
                validation_result=self.make_accepted(service_line_id=2),
            )

    def test_validation_result_decision_id_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_agent_package(
                proposed_decision=self.make_decision(decision_id="dec-1"),
                validation_result=self.make_accepted(decision_id="dec-2"),
            )

    # --- final result mismatches ---
    def test_deterministic_final_disposition_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_package(final_disposition=Disposition.WRITE_OFF)

    def test_manual_review_final_disposition_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_package(
                route_outcome=RouteOutcome.MANUAL_REVIEW,
                final_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
            )

    def test_accepted_final_disposition_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_agent_package(final_disposition=Disposition.WRITE_OFF)

    def test_rejected_validation_requires_manual_review_disposition(self):
        with self.assertRaises(ValueError):
            self.direct_agent_package(
                validation_result=self.make_rejected(),
                final_disposition=Disposition.CORRECT_AND_RESUBMIT,
            )

    def test_terminal_reason_requires_manual_review_disposition(self):
        with self.assertRaises(ValueError):
            self.direct_package(
                route_outcome=RouteOutcome.AGENT_REQUIRED,
                agent_terminal_reason="MODEL_CALL_BUDGET_EXHAUSTED",
                final_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
            )

    def test_final_root_cause_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.direct_package(final_root_cause=RootCause.CODING)


if __name__ == '__main__':
    unittest.main()
