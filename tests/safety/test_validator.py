import unittest
from datetime import date
from claim_agent.contracts import (
    AgentDecision, BaselineFinding, EvidencePassage, Disposition, 
    RootCause, ValidationOutcome, AgentStatus, CriterionAssessment, CriterionStatus,
    PROBLEM_IDENTITY_MISMATCH, PROBLEM_STATUS_MISMATCH, PROBLEM_UNAUTHORIZED_DISPOSITION,
    PROBLEM_PROHIBITED_APPEAL, PROBLEM_MISSING_POLICY_FORMULA, PROBLEM_UNSUPPORTED_CRITERION,
    PROBLEM_MISSING_ENCOUNTER_CITATION, PROBLEM_PROHIBITED_BILLING, PROBLEM_UNAUTHORIZED_OVERRIDE, 
    PROBLEM_DEADLINE_EXPIRED, PROBLEM_FORMULA_NOT_SATISFIED, PROBLEM_MISSING_CITATION, 
    PROBLEM_UNRESOLVED_CITATION
)
from claim_agent.safety.validator import validate_agent_decision

class TestValidatorFaultInjection(unittest.TestCase):
    
    def setUp(self):
        self.finding = BaselineFinding(
            claim_id="CLM-1", service_line_id=1, denial_codes=["PR-1"],
            root_cause=RootCause.MEDICAL_NECESSITY, baseline_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
            rationale="Test", deadline_status="ACTIVE"
        )
        self.decision = AgentDecision(
            decision_id="d1", claim_id="CLM-1", service_line_id=1,
            root_cause=RootCause.MEDICAL_NECESSITY, recommended_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
            agent_status=AgentStatus.COMPLETED, rationale="Test", citation_ids=["enc1@v1#p1"]
        )
        self.evidence = [
            EvidencePassage(passage_id="enc1@v1#p1", source_id="enc1", source_type="encounter_note", version="1", text="text")
        ]
        self.as_of = date(2026, 9, 6)

    def test_accepted_happy_path(self):
        self.decision.criterion_assessments = [
            CriterionAssessment(criterion_id="A", status=CriterionStatus.SUPPORTED, explanation=".", citation_ids=["enc1@v1#p1"])
        ]
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of, policy_formula="A")
        self.assertEqual(result.outcome, ValidationOutcome.ACCEPTED)

    def test_identity_mismatch(self):
        self.decision.claim_id = "WRONG"
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_IDENTITY_MISMATCH)

    def test_identity_mismatch_root_cause(self):
        self.decision.root_cause = RootCause.CODING
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_IDENTITY_MISMATCH)

    def test_status_mismatch(self):
        self.decision.agent_status = AgentStatus.MANUAL_REVIEW_REQUIRED
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_STATUS_MISMATCH)

    def test_status_mismatch_completed_manual(self):
        self.decision.agent_status = AgentStatus.COMPLETED
        self.decision.recommended_disposition = Disposition.MANUAL_REVIEW
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_STATUS_MISMATCH)

    def test_prohibited_patient_billing_co_denial(self):
        self.finding.denial_codes = ["CO-45"]
        self.decision.recommended_disposition = Disposition.BILL_PATIENT
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_PROHIBITED_BILLING)

    def test_contractual_appeal_prohibited(self):
        self.finding.root_cause = RootCause.CONTRACTUAL
        self.decision.root_cause = RootCause.CONTRACTUAL
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_PROHIBITED_APPEAL)

    def test_deadline_expired(self):
        self.finding.deadline_status = "EXPIRED"
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_DEADLINE_EXPIRED)

    def test_string_criteria_formula_not_satisfied(self):
        self.decision.criterion_assessments = [
            CriterionAssessment(criterion_id="D", status=CriterionStatus.SUPPORTED, explanation=".", citation_ids=["enc1@v1#p1"])
        ]
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of, policy_formula="A AND B AND (C OR D)")
        self.assertEqual(result.validation_problems[0].code, PROBLEM_FORMULA_NOT_SATISFIED)

    def test_missing_policy_formula(self):
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of, policy_formula="")
        self.assertEqual(result.validation_problems[0].code, PROBLEM_MISSING_POLICY_FORMULA)

    def test_terminal_missing_evidence(self):
        result = validate_agent_decision(self.decision, self.finding, [], self.as_of)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_MISSING_CITATION)
        self.assertFalse(result.validation_problems[0].is_repairable)

    def test_unknown_criteria_terminal(self):
        self.decision.criterion_assessments = [
            CriterionAssessment(criterion_id="A", status=CriterionStatus.UNKNOWN, explanation=".", citation_ids=["enc1@v1#p1"])
        ]
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of, policy_formula="A")
        problem_codes = [p.code for p in result.validation_problems]
        self.assertIn(PROBLEM_UNSUPPORTED_CRITERION, problem_codes)

if __name__ == '__main__':
    unittest.main()