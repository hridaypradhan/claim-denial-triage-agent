import unittest
from pydantic import ValidationError
from claim_agent.contracts import (
    ToolResult, ToolStatus, ErrorCode, 
    EvidencePassage, ValidationResult, ValidationOutcome,
    AgentDecision, Disposition, RootCause, AgentStatus
)

class TestSchema(unittest.TestCase):
    def test_public_imports(self):
        """Test that public imports work."""
        from claim_agent.contracts import ToolResult, ValidationResult
        self.assertIsNotNone(ToolResult)

    def test_valid_ok_tool_result(self):
        result = ToolResult(
            request_id="req_1",
            tool_name="tool_1",
            status=ToolStatus.OK
        )
        self.assertEqual(result.status, ToolStatus.OK)

    def test_valid_retryable_transient_failure(self):
        result = ToolResult(
            request_id="req_2",
            tool_name="tool_1",
            status=ToolStatus.ERROR,
            error_code=ErrorCode.TRANSIENT_FAILURE,
            retryable=True
        )
        self.assertTrue(result.retryable)

    def test_retryable_timeout(self):
        result = ToolResult(
            request_id="req_3",
            tool_name="tool_1",
            status=ToolStatus.ERROR,
            error_code=ErrorCode.TIMEOUT,
            retryable=True
        )
        self.assertTrue(result.retryable)

    def test_retryable_no_match_rejected(self):
        with self.assertRaises(ValidationError):
            ToolResult(
                request_id="req_4",
                tool_name="tool_1",
                status=ToolStatus.ERROR,
                error_code=ErrorCode.NO_MATCH,
                retryable=True
            )

    def test_versioned_source_ids_deterministic_order(self):
        p1 = EvidencePassage(passage_id="docA@v1#p1", source_id="docA", source_type="policy", version="1", text="text1")
        p2 = EvidencePassage(passage_id="docB@v2#p1", source_id="docB", source_type="policy", version="2", text="text2")
        p3 = EvidencePassage(passage_id="docA@v1#p2", source_id="docA", source_type="policy", version="1", text="text3")
        
        result = ToolResult(
            request_id="req_5",
            tool_name="tool_1",
            status=ToolStatus.OK,
            evidence_passages=[p1, p2, p3],
            source_ids=["docA@v1", "docB@v2"]
        )
        self.assertEqual(result.source_ids, ["docA@v1", "docB@v2"])

    def test_accepted_result_matching_identity(self):
        decision = AgentDecision(
            decision_id="d1", claim_id="c1", service_line_id=1,
            root_cause=RootCause.CODING, recommended_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
            agent_status=AgentStatus.COMPLETED, rationale="rationale"
        )
        result = ValidationResult(
            decision_id="d1", claim_id="c1", service_line_id=1,
            outcome=ValidationOutcome.ACCEPTED, validated_decision=decision
        )
        self.assertEqual(result.outcome, ValidationOutcome.ACCEPTED)

    def test_accepted_result_mismatched_identity_rejected(self):
        decision = AgentDecision(
            decision_id="d2", claim_id="c1", service_line_id=1,
            root_cause=RootCause.CODING, recommended_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
            agent_status=AgentStatus.COMPLETED, rationale="rationale"
        )
        with self.assertRaises(ValidationError):
            ValidationResult(
                decision_id="d1", claim_id="c1", service_line_id=1,
                outcome=ValidationOutcome.ACCEPTED, validated_decision=decision
            )

    def test_repairable_result_without_validated_decision(self):
        result = ValidationResult(
            decision_id="d1", claim_id="c1", service_line_id=1,
            outcome=ValidationOutcome.REPAIRABLE
        )
        self.assertIsNone(result.validated_decision)

    def test_manual_review_result_with_reason(self):
        result = ValidationResult(
            decision_id="d1", claim_id="c1", service_line_id=1,
            outcome=ValidationOutcome.REJECTED_MANUAL_REVIEW,
            manual_review_reasons=["reason"]
        )
        self.assertEqual(len(result.manual_review_reasons), 1)

if __name__ == '__main__':
    unittest.main()