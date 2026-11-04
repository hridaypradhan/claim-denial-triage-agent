from typing import List
from src.claim_agent.contracts.schema import AgentDecision, ValidationResult, EvidencePassage, Disposition

def validate_agent_decision(
    decision: AgentDecision, 
    retrieved_evidence: List[EvidencePassage], 
    baseline_disposition: Disposition
) -> ValidationResult:
    """
    Evaluates the LLM's AgentDecision against strict deterministic safety boundaries.
    """
    
    # 1. Structural Citation Check (No Hallucinations)
    # Ensure every citation the LLM used actually exists in the evidence retrieved by Silas's tools.
    valid_passage_ids = {passage.passage_id for passage in retrieved_evidence}
    for citation in decision.citation_ids:
        if citation not in valid_passage_ids:
            return ValidationResult(
                is_valid=False,
                escalation_reason=f"LLM Hallucination: Cited passage '{citation}' was not found in retrieved evidence."
            )
            
    # 2. Evidence Support Check
    # If the agent tried to make a decision but couldn't find any evidence, it must be escalated.
    if not retrieved_evidence and decision.action_taken != Disposition.MANUAL_REVIEW.value:
        return ValidationResult(
            is_valid=False,
            escalation_reason="Unsupported Evidence: Agent attempted to resolve the claim without cited evidence."
        )

    # 3. Patient Billing Restriction
    # Safety boundary: If the baseline didn't authorize billing the patient, the agent cannot override it.
    if decision.action_taken == Disposition.BILL_PATIENT.value and baseline_disposition != Disposition.BILL_PATIENT:
        return ValidationResult(
            is_valid=False,
            escalation_reason="Safety Violation: Agent attempted to bill patient, but baseline did not authorize."
        )

    # 4. Allowed Dispositions Check
    # Ensure the agent only picked an action from our strictly defined list.
    allowed_actions = [d.value for d in Disposition]
    if decision.action_taken not in allowed_actions:
        return ValidationResult(
            is_valid=False,
            escalation_reason=f"Invalid Action: '{decision.action_taken}' is not a recognized disposition."
        )

    # If it passes all checks, it is valid!
    # Note: approval_state ALWAYS defaults to PENDING_REVIEW per the TEAM_DEVELOPMENT_GUIDE.
    return ValidationResult(
        is_valid=True,
        approval_state="PENDING_REVIEW",
        escalation_reason=None
    )