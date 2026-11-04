from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum
from datetime import date

# --- Enums derived from the frozen baseline rules ---

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
    CONFLICTING = "CONFLICTING"
    PRESENT = "PRESENT"

# --- Contract v0.1: Fields needed in ToolResult and EvidencePassage (Silas) ---

class ToolResult(BaseModel):
    status: str = Field(description="The status of the tool execution (e.g., 'SUCCESS', 'FAILED')")
    data: dict = Field(description="The retrieved data from the tool")
    source_id: str = Field(description="The unique identifier of the source document for citation checks")
    error_code: Optional[str] = Field(None, description="Explicit error code for routing to MANUAL_REVIEW on failure")

class EvidencePassage(BaseModel):
    passage_id: str = Field(description="Unique ID for this specific passage to be used in citations")
    effective_date: date = Field(description="The effective date of the policy/record for deterministic checks")
    content: str = Field(description="The actual text content of the evidence passage")

# --- Contract v0.1: Fields needed in BaselineFinding and RouteDecision (Hriday) ---

class BaselineFinding(BaseModel):
    baseline_disposition: Disposition = Field(description="The original deterministic decision from the frozen baseline")
    evidence_status: EvidenceStatus = Field(description="Current status of the required evidence")
    deadline_date: Optional[date] = Field(None, description="The deadline for the primary disposition, if applicable")

class RouteDecision(BaseModel):
    route_to_agent: bool = Field(description="True if the LLM should handle this case, False if it goes to manual review")
    routing_reason: str = Field(description="The specific reason for the routing decision")

# --- Contract v0.1: Fields needed in AgentDecision and ValidationResult (Hriday/Neha) ---

class AgentDecision(BaseModel):
    action_taken: str = Field(description="The specific action the agent has decided to take")
    rationale: str = Field(description="The agent's explanation for its decision")
    citation_ids: List[str] = Field(description="List of passage_ids that support the rationale")

class ValidationResult(BaseModel):
    is_valid: bool = Field(description="True if the agent decision passes all structural and safety checks")
    approval_state: str = Field(default="PENDING_REVIEW", description="The final state; must always default to PENDING_REVIEW")
    escalation_reason: Optional[str] = Field(None, description="If is_valid is False, the specific safety rule that was violated")