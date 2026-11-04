from pydantic import BaseModel, Field
from typing import Optional
from src.claim_agent.contracts.schema import (
    BaselineFinding, 
    RouteDecision, 
    AgentDecision, 
    ValidationResult,
    Disposition
)

class ReviewPackage(BaseModel):
    claim_id: str
    baseline_finding: BaselineFinding
    route_decision: RouteDecision
    agent_decision: Optional[AgentDecision] = None
    validation_result: ValidationResult
    final_state: str = Field(default="PENDING_REVIEW", description="Always requires human review")

def create_review_package(
    claim_id: str,
    finding: BaselineFinding,
    route: RouteDecision,
    validation: ValidationResult,
    agent_decision: Optional[AgentDecision] = None
) -> ReviewPackage:
    """
    Assembles the final review package for human reviewers.
    Enforces the rule that all outputs are PENDING_REVIEW.
    """
    
    # Forcefully override the action to MANUAL_REVIEW if the LLM violated safety rules
    if not validation.is_valid and agent_decision:
        agent_decision.action_taken = Disposition.MANUAL_REVIEW.value
        agent_decision.rationale = f"ESCALATED BY SAFETY LAYER: {validation.escalation_reason} | Original rationale: {agent_decision.rationale}"
        
    return ReviewPackage(
        claim_id=claim_id,
        baseline_finding=finding,
        route_decision=route,
        agent_decision=agent_decision,
        validation_result=validation,
        final_state="PENDING_REVIEW"
    )