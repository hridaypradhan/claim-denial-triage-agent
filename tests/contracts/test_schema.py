import pytest
from pydantic import ValidationError
from datetime import date

# 1. Test that public imports work perfectly
from claim_agent.contracts import (
    ToolResult, ToolStatus, ErrorCode, 
    EvidencePassage, AgentDecision, ValidationResult
)

def test_evidence_passage_valid_id():
    """Validates that the strict formatting rules for passage IDs work."""
    passage = EvidencePassage(
        passage_id="doc123@v1.0#para1",
        source_id="doc123",
        source_type="policy",
        version="1.0",
        text="Sample text."
    )
    assert passage.passage_id == "doc123@v1.0#para1"

def test_evidence_passage_invalid_id_rejected():
    """Validates that malformed passage IDs are rejected."""
    with pytest.raises(ValidationError):
        EvidencePassage(
            passage_id="bad_format_id",
            source_id="doc123",
            source_type="policy",
            version="1.0",
            text="Sample text."
        )

def test_tool_result_valid_combinations():
    """Valid objects are accepted: OK status must have no errors."""
    result = ToolResult(
        request_id="req_1",
        tool_name="policy_retrieval",
        status=ToolStatus.OK,
        retryable=False
    )
    assert result.status == ToolStatus.OK
    assert result.error_code is None

def test_tool_result_invalid_combinations_rejected():
    """Invalid field combinations are rejected: OK status cannot have an error code."""
    with pytest.raises(ValidationError):
        ToolResult(
            request_id="req_2",
            tool_name="policy_retrieval",
            status=ToolStatus.OK,
            error_code=ErrorCode.NO_MATCH, # This should trigger the validator
            retryable=False
        )

def test_tool_result_error_requires_code():
    """ERROR status must explicitly declare an error code."""
    with pytest.raises(ValidationError):
        ToolResult(
            request_id="req_3",
            tool_name="policy_retrieval",
            status=ToolStatus.ERROR,
            error_code=None, # Missing required error code
            retryable=False
        )