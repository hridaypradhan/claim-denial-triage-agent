import unittest
from datetime import date
from claim_agent.contracts import (
    BaselineFinding, AgentDecision, ValidationResult, ValidationProblem,
    Disposition, RootCause, RouteOutcome, AgentStatus, ValidationOutcome,
    EvidencePassage
)
from claim_agent.output.package import create_output_package

class TestOutputPackage(unittest.TestCase):

    def setUp(self):
        self.finding = BaselineFinding(
            claim_id="CLM-100", service_line_id=1, denial_codes=["CO-45"],
            root_cause=RootCause.MEDICAL_NECESSITY, baseline_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
            rationale="Test finding", deadline_status="ACTIVE"
        )

    def test_deterministic_only_package(self):
        pkg = create_output_package(
            run_id="run-1", package_version="v1", knowledge_snapshot_id="snap-1",
            finding=self.finding, route_outcome=RouteOutcome.DETERMINISTIC_ONLY
        )
        self.assertEqual(pkg.final_disposition, Disposition.APPEAL_WITH_DOCUMENTATION)
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
            proposed_decision=None, validation_result=None, agent_terminal_reason="MODEL_CALL_BUDGET_EXHAUSTED"
        )
        self.assertEqual(pkg.agent_terminal_reason, "MODEL_CALL_BUDGET_EXHAUSTED")
        self.assertIn("MODEL_CALL_BUDGET_EXHAUSTED", pkg.manual_review_reasons)

if __name__ == '__main__':
    unittest.main()