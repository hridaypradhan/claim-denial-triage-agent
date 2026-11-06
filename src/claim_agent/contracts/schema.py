from datetime import date
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "0.1"

# --- Validation Problem Codes ---
PROBLEM_PROHIBITED_BILLING = "PROHIBITED_PATIENT_BILLING"
PROBLEM_UNAUTHORIZED_OVERRIDE = "UNAUTHORIZED_OVERRIDE"
PROBLEM_DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
PROBLEM_FORMULA_NOT_SATISFIED = "CRITERIA_FORMULA_NOT_SATISFIED"
PROBLEM_MISSING_CITATION = "MISSING_CITATION"
PROBLEM_UNRESOLVED_CITATION = "UNRESOLVED_CITATION"
PROBLEM_UNRESOLVED_CRITERION = "UNRESOLVED_CRITERION_CITATION"
PROBLEM_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
PROBLEM_STATUS_MISMATCH = "STATUS_DISPOSITION_MISMATCH"
PROBLEM_UNAUTHORIZED_DISPOSITION = "UNAUTHORIZED_DISPOSITION"
PROBLEM_PROHIBITED_APPEAL = "PROHIBITED_APPEAL"
PROBLEM_MISSING_POLICY_FORMULA = "MISSING_POLICY_FORMULA"
PROBLEM_UNSUPPORTED_CRITERION = "UNSUPPORTED_CRITERION"
PROBLEM_MISSING_ENCOUNTER_CITATION = "MISSING_ENCOUNTER_CITATION"

# --- Safety Escalation Reasons ---
ESCALATION_PROHIBITED_BILLING = "SAFETY_ESCALATION_PROHIBITED_BILLING"
ESCALATION_UNAUTHORIZED_OVERRIDE = "SAFETY_ESCALATION_UNAUTHORIZED_OVERRIDE"
ESCALATION_DEADLINE_EXPIRED = "SAFETY_ESCALATION_DEADLINE_EXPIRED"
ESCALATION_FORMULA_NOT_SATISFIED = "SAFETY_ESCALATION_FORMULA_NOT_SATISFIED"
ESCALATION_MISSING_EVIDENCE = "SAFETY_ESCALATION_MISSING_EVIDENCE"
ESCALATION_IDENTITY_MISMATCH = "SAFETY_ESCALATION_IDENTITY_MISMATCH"
ESCALATION_STATUS_MISMATCH = "SAFETY_ESCALATION_STATUS_MISMATCH"
ESCALATION_UNAUTHORIZED_DISPOSITION = "SAFETY_ESCALATION_UNAUTHORIZED_DISPOSITION"
ESCALATION_PROHIBITED_APPEAL = "SAFETY_ESCALATION_PROHIBITED_APPEAL"
ESCALATION_MISSING_POLICY_FORMULA = "SAFETY_ESCALATION_MISSING_POLICY_FORMULA"
ESCALATION_UNSUPPORTED_CRITERION = "SAFETY_ESCALATION_UNSUPPORTED_CRITERION"
ESCALATION_MISSING_ENCOUNTER_CITATION = "SAFETY_ESCALATION_MISSING_ENCOUNTER_CITATION"

# --- Enums ---
class Disposition(str, Enum):
    APPEAL_WITH_DOCUMENTATION = "APPEAL_WITH_DOCUMENTATION"
    CORRECT_AND_RESUBMIT = "CORRECT_AND_RESUBMIT"
    ROUTE_TO_CORRECT_PAYER = "ROUTE_TO_CORRECT_PAYER"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    BILL_PATIENT = "BILL_PATIENT"
    WRITE_OFF = "WRITE_OFF"
    NO_ACTION_CONTRACTUAL = "NO_ACTION_CONTRACTUAL"


class RootCause(str, Enum):
    AUTHORIZATION = "AUTHORIZATION"
    CODING = "CODING"
    MEDICAL_NECESSITY = "MEDICAL_NECESSITY"
    BUNDLING_NCCI = "BUNDLING_NCCI"
    DUPLICATE = "DUPLICATE"
    TIMELY_FILING = "TIMELY_FILING"
    REGISTRATION_ELIGIBILITY = "REGISTRATION_ELIGIBILITY"
    COORDINATION_OF_BENEFITS = "COORDINATION_OF_BENEFITS"
    NON_COVERED_BENEFIT = "NON_COVERED_BENEFIT"
    CONTRACTUAL = "CONTRACTUAL"
    UNKNOWN = "UNKNOWN"


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
class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# --- Contract v0.1: Tools ---
class EvidencePassage(StrictContract):
    passage_id: str = Field(description="Unique ID for this specific passage")
    source_id: str = Field(description="The source document identifier")
    source_type: str = Field(description="The type of the source document")
    version: str = Field(description="The version of the source document")
    effective_from: Optional[date] = Field(None, description="Start of effective date range")
    effective_to: Optional[date] = Field(None, description="End of effective date range")
    text: str = Field(description="The actual text content")

    @model_validator(mode="after")
    def validate_passage_id_format(self):
        expected_prefix = f"{self.source_id}@v{self.version}#"
        if not self.passage_id.startswith(expected_prefix):
            raise ValueError(
                f"passage_id '{self.passage_id}' must follow format "
                "<source_id>@v<version>#<passage>"
            )
        return self


class ToolResult(StrictContract):
    contract_version: str = Field(default=CONTRACT_VERSION)
    request_id: str = Field(description="Unique identifier for the tool request")
    tool_name: str = Field(description="Name of the tool executed")
    status: ToolStatus
    data: Dict[str, Any] = Field(default_factory=dict)
    source_ids: List[str] = Field(default_factory=list)
    evidence_passages: List[EvidencePassage] = Field(default_factory=list)
    error_code: Optional[ErrorCode] = Field(None)
    retryable: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_tool_relationships(self):
        if self.status == ToolStatus.OK:
            if self.error_code is not None or self.retryable:
                raise ValueError("error_code must be null and retryable False when status is OK")
        else:
            if self.error_code is None:
                raise ValueError("error_code is required when status is ERROR")
            if self.retryable and self.error_code not in (
                ErrorCode.TRANSIENT_FAILURE,
                ErrorCode.TIMEOUT,
            ):
                raise ValueError(
                    "retryable=True is only permitted for TRANSIENT_FAILURE and TIMEOUT"
                )

        expected_source_ids = []
        seen_source_ids = set()
        for passage in self.evidence_passages:
            versioned_source_id = f"{passage.source_id}@v{passage.version}"
            if versioned_source_id not in seen_source_ids:
                seen_source_ids.add(versioned_source_id)
                expected_source_ids.append(versioned_source_id)

        if self.evidence_passages and self.source_ids != expected_source_ids:
            raise ValueError(
                "source_ids must exactly match the unique versioned document IDs "
                "in first-appearance order"
            )
        return self


# --- Contract v0.1: Routing ---
class BaselineFinding(StrictContract):
    contract_version: str = Field(default=CONTRACT_VERSION)
    claim_id: str
    service_line_id: int
    denial_codes: List[str]
    root_cause: RootCause
    baseline_disposition: Disposition
    required_evidence: List[str] = Field(default_factory=list)
    rationale: str
    filing_deadline: Optional[date] = Field(None)
    deadline_status: str


class RouteDecision(StrictContract):
    contract_version: str = Field(default=CONTRACT_VERSION)
    claim_id: str
    service_line_id: int
    outcome: RouteOutcome
    reason_code: str
    routing_reason: str
    authorized_tools: List[str] = Field(default_factory=list)

# --- Contract v0.1: Agent Decisions ---
class CriterionAssessment(StrictContract):
    criterion_id: str
    status: CriterionStatus
    explanation: str
    citation_ids: List[str] = Field(default_factory=list)


class AgentDecision(StrictContract):
    contract_version: str = Field(default=CONTRACT_VERSION)
    decision_id: str
    claim_id: str
    service_line_id: int
    root_cause: RootCause
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
    contract_version: str = Field(default=CONTRACT_VERSION)
    decision_id: str
    claim_id: str
    service_line_id: int
    outcome: ValidationOutcome
    approval_state: Literal["PENDING_REVIEW"] = Field(default="PENDING_REVIEW")
    validation_problems: List[ValidationProblem] = Field(default_factory=list)
    validated_decision: Optional[AgentDecision] = Field(None)
    manual_review_reasons: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_outcomes(self):
        if self.outcome == ValidationOutcome.ACCEPTED:
            if not self.validated_decision:
                raise ValueError("validated_decision is required for ACCEPTED outcome.")
            if self.validated_decision.decision_id != self.decision_id:
                raise ValueError("decision_id mismatch between ValidationResult and validated_decision")
            if self.validated_decision.claim_id != self.claim_id:
                raise ValueError("claim_id mismatch between ValidationResult and validated_decision")
            if self.validated_decision.service_line_id != self.service_line_id:
                raise ValueError("service_line_id mismatch between ValidationResult and validated_decision")
        elif self.outcome in (
            ValidationOutcome.REPAIRABLE,
            ValidationOutcome.REJECTED_MANUAL_REVIEW,
        ):
            if self.validated_decision:
                raise ValueError(
                    "validated_decision must be omitted for repairable or manual "
                    "review outcomes."
                )
            if (
                self.outcome == ValidationOutcome.REJECTED_MANUAL_REVIEW
                and not self.manual_review_reasons
            ):
                raise ValueError(
                    "At least one manual_review_reasons is required for "
                    "REJECTED_MANUAL_REVIEW."
                )
        return self
