from datetime import date
from typing import Tuple

# Assuming the baseline code is accessible in your python path
from baseline.triage.rules import triage_claim
from src.claim_agent.contracts.schema import BaselineFinding, RouteDecision, Disposition, EvidenceStatus

def analyze_and_route_claim(claim_data: dict, as_of: date = None) -> Tuple[BaselineFinding, RouteDecision]:
    """
    Wraps the frozen deterministic baseline. 
    It evaluates the claim and decides if the LLM should handle it or if it requires manual review.
    """
    if as_of is None:
        as_of = date.today()

    # 1. Run the frozen deterministic triage logic
    triage_result = triage_claim(claim_data, as_of)

    # 2. Extract key baseline outputs
    primary_disposition = Disposition(triage_result["primary_disposition"])
    
    # We look at the first finding to grab the deadline (the baseline sorts by priority)
    primary_finding = triage_result["findings"][0]
    raw_deadline = primary_finding.get("deadline")
    deadline_date = date.fromisoformat(raw_deadline) if raw_deadline else None
    
    evidence_needed = primary_finding.get("evidence_needed", [])
    
    # Determine evidence status
    # If the baseline says we need evidence, it's MISSING. If not, it's PRESENT.
    if evidence_needed:
        evidence_status = EvidenceStatus.MISSING
    else:
        evidence_status = EvidenceStatus.PRESENT

    # 3. Create the BaselineFinding contract
    finding = BaselineFinding(
        baseline_disposition=primary_disposition,
        evidence_status=evidence_status,
        deadline_date=deadline_date
    )

    # 4. Deterministic Routing Logic
    # We NEVER route to the LLM if the baseline already determined it needs manual review,
    # or if there are explicit warnings (like a low confidence finding or unknown payer).
    if primary_disposition == Disposition.MANUAL_REVIEW:
        route = RouteDecision(
            route_to_agent=False,
            routing_reason="Baseline determined MANUAL_REVIEW is required."
        )
    elif triage_result.get("warnings"):
        route = RouteDecision(
            route_to_agent=False,
            routing_reason=f"Baseline generated warnings: {', '.join(triage_result['warnings'])}"
        )
    # The LLM is only useful if we need to retrieve and evaluate evidence (like policies or clinical notes)
    elif evidence_status == EvidenceStatus.MISSING:
        route = RouteDecision(
            route_to_agent=True,
            routing_reason="Evidence is missing; routing to agent for retrieval and analysis."
        )
    else:
        # If no evidence is missing and it's not manual review, the baseline solved it (e.g. a simple WRITE_OFF)
        route = RouteDecision(
            route_to_agent=False,
            routing_reason="Baseline reached a deterministic conclusion without requiring additional evidence."
        )

    return finding, route