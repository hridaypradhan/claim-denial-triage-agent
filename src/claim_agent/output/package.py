from pydantic import BaseModel, Field, model_validator
from typing import Optional, Literal

# Fix 7: Use the public package boundary for imports
from claim_agent.contracts import (
    BaselineFinding, 
    RouteDecision, 
    AgentDecision, 
    ValidationResult,
    RouteOutcome,
    ValidationOutcome
)

class ReviewPackage(BaseModel):
    """
    The final immutable output package presented for human review.
    Preserves original proposals, encapsulates all routing and safety logic,
    and provides references to the exact retrieved passage IDs and baseline rationale.
    """
    claim_id: str
    service_line_id: int
    baseline_finding: BaselineFinding
    route_decision: RouteDecision
    # Fix 9: Rename to proposed_decision
    proposed_decision: Optional[AgentDecision] = None
    validation_result: Optional[ValidationResult] = None
    
    # Fix 2: Enforce PENDING_REVIEW using a Literal so no other string is allowed
    final_state: Literal["PENDING_REVIEW"] = Field(
        default="PENDING_REVIEW", 
        description="Always requires human review. Agent cannot autonomously complete claims."
    )

    # Fix 3 & 4: Cross-contract consistency and valid package shapes
    @model_validator(mode='after')
    def validate_cross_contract_consistency(self):
        # Match Claim IDs
        if self.baseline_finding.claim_id != self.claim_id:
            raise ValueError(f"Baseline claim_id '{self.baseline_finding.claim_id}' does not match package claim_id '{self.claim_id}'")
        if self.proposed_decision and self.proposed_decision.claim_id != self.claim_id:
            raise ValueError("Proposed decision claim_id does not match package claim_id")

        # Match Service Line IDs
        if self.baseline_finding.service_line_id != self.service_line_id:
            raise ValueError("Baseline service_line_id does not match package service_line_id")
        if self.proposed_decision and self.proposed_decision.service_line_id != self.service_line_id:
            raise ValueError("Proposed decision service_line_id does not match package service_line_id")

        # Route Consistency
        if self.route_decision.outcome in (RouteOutcome.DETERMINISTIC_ONLY, RouteOutcome.MANUAL_REVIEW):
            if self.proposed_decision is not None:
                raise ValueError("proposed_decision must be None for routes that forbid the agent")
        
        # Validation Consistency
        if self.validation_result:
            if self.validation_result.outcome == ValidationOutcome.ACCEPTED:
                if not self.validation_result.validated_decision:
                    raise ValueError("An ACCEPTED validation_result must contain a validated_decision")
            elif self.validation_result.outcome in (ValidationOutcome.REPAIRABLE, ValidationOutcome.REJECTED_MANUAL_REVIEW):
                if self.validation_result.validated_decision:
                    raise ValueError("Terminal or repairable validation results cannot masquerade as accepted with a validated_decision")

        return self

# Fix 9: Rename parameters for clarity
def create_review_package(
    claim_id: str,
    service_line_id: int,
    baseline_finding: BaselineFinding,
    route_decision: RouteDecision,
    validation_result: Optional[ValidationResult] = None,
    proposed_decision: Optional[AgentDecision] = None
) -> ReviewPackage:
    """
    Assembles the final review package for human reviewers.
    Escalations and mutations are represented in the typed ValidationResult.
    """
    
    # Fix 1 & 5: Removed the in-place mutation of agent_decision.
    # The rejected proposal is preserved perfectly unchanged.
    
    return ReviewPackage(
        claim_id=claim_id,
        service_line_id=service_line_id,
        baseline_finding=baseline_finding,
        route_decision=route_decision,
        proposed_decision=proposed_decision,
        validation_result=validation_result,
        final_state="PENDING_REVIEW"
    )
