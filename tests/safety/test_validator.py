import unittest
from datetime import date
from claim_agent.contracts import (
    AgentDecision, BaselineFinding, EvidencePassage, Disposition, 
    RootCause, ValidationOutcome, AgentStatus, CriterionAssessment, CriterionStatus,
    PROBLEM_PROHIBITED_BILLING, PROBLEM_UNAUTHORIZED_OVERRIDE, 
    PROBLEM_DEADLINE_EXPIRED, PROBLEM_FORMULA_NOT_SATISFIED, 
    PROBLEM_MISSING_CITATION, PROBLEM_UNRESOLVED_CITATION
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
            agent_status=AgentStatus.COMPLETED, rationale="Test", citation_ids=["doc1@v1#p1"]
        )
        self.evidence = [
            EvidencePassage(passage_id="doc1@v1#p1", source_id="doc1", source_type="policy", version="1", text="text")
        ]
        self.as_of = date(2026, 9, 6)

    def test_accepted_happy_path(self):
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.outcome, ValidationOutcome.ACCEPTED)
        self.assertEqual(result.validated_decision.decision_id, "d1")

    def test_prohibited_patient_billing_co_denial(self):
        self.finding.denial_codes = ["CO-45"]
        self.decision.recommended_disposition = Disposition.BILL_PATIENT
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.outcome, ValidationOutcome.REJECTED_MANUAL_REVIEW)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_PROHIBITED_BILLING)

    def test_unauthorized_override(self):
        self.finding.baseline_disposition = Disposition.MANUAL_REVIEW
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.outcome, ValidationOutcome.REJECTED_MANUAL_REVIEW)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_UNAUTHORIZED_OVERRIDE)

    def test_deadline_expired(self):
        self.finding.filing_deadline = date(2026, 9, 1) # Before as_of
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.outcome, ValidationOutcome.REJECTED_MANUAL_REVIEW)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_DEADLINE_EXPIRED)

    def test_criteria_formula_not_satisfied(self):
        self.decision.criterion_assessments = [
            CriterionAssessment(criterion_id="A", status=CriterionStatus.NOT_MET, explanation=".", citation_ids=["doc1@v1#p1"])
        ]
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of, policy_formula=["A", "AND", "B"])
        self.assertEqual(result.outcome, ValidationOutcome.REJECTED_MANUAL_REVIEW)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_FORMULA_NOT_SATISFIED)

    def test_terminal_missing_evidence(self):
        self.decision.citation_ids = []
        result = validate_agent_decision(self.decision, self.finding, [], self.as_of)
        self.assertEqual(result.outcome, ValidationOutcome.REJECTED_MANUAL_REVIEW)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_MISSING_CITATION)

    def test_repairable_missing_evidence(self):
        self.decision.citation_ids = []
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.outcome, ValidationOutcome.REPAIRABLE)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_MISSING_CITATION)

    def test_unresolved_citation(self):
        self.decision.citation_ids = ["fake_doc@v1#p1"]
        result = validate_agent_decision(self.decision, self.finding, self.evidence, self.as_of)
        self.assertEqual(result.outcome, ValidationOutcome.REPAIRABLE)
        self.assertEqual(result.validation_problems[0].code, PROBLEM_UNRESOLVED_CITATION)

if __name__ == '__main__':
    unittest.main()