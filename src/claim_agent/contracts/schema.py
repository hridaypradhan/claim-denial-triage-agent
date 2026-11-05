from pydantic import BaseModel, Field, model_validator, ConfigDict
from typing import List, Optional, Dict, Any, Literal
from enum import Enum
from datetime import date

# --- Enums ---

class Disposition(str, Enum):
    APPEAL_WITH_DOCUMENTATION = "APPEAL_WITH_DOCUMENTATION"
    CORRECT_AND_RESUBMIT = "CORRECT_AND_RESUBMIT"
    ROUTE_TO_CORRECT_PAYER = "ROUTE_TO_CORRECT_PAYER"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    BILL_PATIENT = "BILL_PATIENT"
    WRITE_OFF = "WRITE_OFF"
    NO_ACTION_CONTRACTUAL = "NO_ACTION_CONTRACTUAL"

class EvidenceStatus(str, Enum):
    MISSING = "MISSING"
    PRESENT = "PRESENT"
    CONFLICTING = "CONFLICTING"
    NOT_APPLICABLE = "NOT_APPLICABLE"

class RouteOutcome(str, Enum):
    DETERMINISTIC_ONLY = "DETERMINISTIC_ONLY"
    AGENT_REQUIRED = "AGENT_REQUIRED"
    MANUAL_REVIEW = "MANUAL_REVIEW"

class ToolStatus(str, Enum):
    OK = "OK"
    ERROR = "ERROR"

class ErrorCode(str, Enum):
    NO_MATCH = "NO_MATCH"
    AMBIGUOUS_POLICY = "AMBIGUOUS_POLICY"
    DOCUMENT_UNAVAILABLE = "DOCUMENT_UNAVAILABLE"
    MALFORMED_REQUEST = "MALFORMED_REQUEST"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    TIMEOUT = "TIMEOUT"

class ValidationOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    REPAIRABLE = "REPAIRABLE"
    REJECTED_MANUAL_REVIEW = "REJECTED_MANUAL_REVIEW"

class AgentStatus(str, Enum):
    COMPLETED = "COMPLETED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"

class CriterionStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    NOT_MET = "NOT_MET"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"

# --- Shared Base Configuration ---
# Enforces strict typing and forbids unknown fields to prevent LLM hallucinations
class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

# --- Contract v0.1: Tools (Silas) ---

class EvidencePassage(StrictContract):
    passage_id: str = Field(description="Unique ID for this specific passage")
    source_id: str = Field(description="The source document identifier")
    source_type: str = Field(description="The type of the source document")
    version: str = Field(description="The version of the source document")
    effective_from: Optional[date] = Field(None, description="Start of effective date range")
    effective_to: Optional[date] = Field(None, description="End of effective date range")
    text: str = Field(description="The actual text content")

    @model_validator(mode='after')
    def validate_passage_id_format(self):
        expected_prefix = f"{self.source_id}@v{self.version}#"
        if not self.passage_id.startswith(expected_prefix):
            raise ValueError(f"passage_id '{self.passage_id}' must follow format <source_id>@v<version>#<passage>")
        return self

class ToolResult(StrictContract):
    request_id: str = Field(description="Unique identifier for the tool request")
    tool_name: str = Field(description="Name of the tool executed")
    status: ToolStatus
    data: Dict[str, Any] = Field(default_factory=dict)
    source_ids: List[str] = Field(default_factory=list)
    evidence_passages: List[EvidencePassage] = Field(default_factory=list)
    error_code: Optional[ErrorCode] = Field(None)
    retryable: bool = Field(default=False)

    @model_validator(mode='after')
    def validate_status_relationships(self):
        if self.status == ToolStatus.OK:
            if self.error_code is not None or self.retryable:
                raise ValueError("error_code must be null and retryable False when status is OK")
        else:
            if self.error_code is None:
                raise ValueError("error_code is required when status is ERROR")
        return self

# --- Contract v0.1: Routing (Hriday) ---

class BaselineFinding(StrictContract):
    contract_version: str = Field(default="0.1")
    claim_id: str
    service_line_id: int
    denial_codes: List[str]
    root_cause: str
    baseline_disposition: Disposition
    required_evidence: List[str] = Field(default_factory=list)
    rationale: str
    filing_deadline: Optional[date] = Field(None)
    deadline_status: str

class RouteDecision(StrictContract):
    outcome: RouteOutcome
    reason_code: str
    routing_reason: str
    authorized_tools: List[str] = Field(default_factory=list)

# --- Contract v0.1: Agent Decisions (Issue #6) ---

class CriterionAssessment(StrictContract):
    criterion_id: str
    status: CriterionStatus
    explanation: str
    citation_ids: List[str] = Field(default_factory=list)

class AgentDecision(StrictContract):
    contract_version: str = Field(default="0.1")
    claim_id: str
    service_line_id: int
    recommended_disposition: Disposition
    actions: List[str] = Field(default_factory=list)
    criterion_assessments: List[CriterionAssessment] = Field(default_factory=list)
    evidence_gaps: List[str] = Field(default_factory=list)
    agent_status: AgentStatus
    manual_review_reasons: List[str] = Field(default_factory=list)
    rationale: str
    citation_ids: List[str] = Field(default_factory=list)

class ValidationProblem(StrictContract):
    code: str
    field_path: str
    message: str
    is_repairable: bool

class ValidationResult(StrictContract):
    outcome: ValidationOutcome
    approval_state: Literal["PENDING_REVIEW"] = Field(default="PENDING_REVIEW")
    validation_problems: List[ValidationProblem] = Field(default_factory=list)
    validated_decision: Optional[AgentDecision] = Field(None, description="Present only if ACCEPTED")
    manual_review_reasons: List[str] = Field(default_factory=list)
