from datetime import date
from typing import Tuple

from baseline.triage.claim import Claim
from baseline.triage.rules import triage_claim
from claim_agent.contracts.schema import (
    BaselineFinding, 
    RouteDecision, 
    Disposition, 
    EvidenceStatus, 
    RouteOutcome
)

def analyze_and_route_claim(claim: Claim, as_of: date) -> Tuple[BaselineFinding, RouteDecision]:
    """
    Wraps the frozen deterministic baseline. Evaluates the Claim object 
    and outputs deterministic routing contracts.
    """
    triage_result = triage_claim(claim, as_of)
    primary_disposition = Disposition(triage_result["primary_disposition"])
    
    # Fix: Find the exact finding that drove the primary disposition (Multi-denial safety)
    primary_finding = next(
        (f for f in triage_result["findings"] if f["disposition"] == primary_disposition.value),
        triage_result["findings"][0]
    )
    
    raw_deadline = primary_finding.get("deadline")
    deadline_date = date.fromisoformat(raw_deadline) if raw_deadline else None
    
    days_remaining = primary_finding.get("days_remaining")
    if days_remaining is None:
        deadline_status = "NONE"
    elif days_remaining < 0:
        deadline_status = "EXPIRED"
    else:
        deadline_status = "ACTIVE"
        
    evidence_needed = primary_finding.get("evidence_needed", [])
    
    # Fix: Do not infer PRESENT just because evidence is empty
    if not evidence_needed:
        evidence_status = EvidenceStatus.NOT_APPLICABLE
    else:
        evidence_status = EvidenceStatus.MISSING

    finding = BaselineFinding(
        contract_version="0.1",
        claim_id=triage_result["claim_id"],
        service_line_id=primary_finding["service_line"],
        denial_codes=[primary_finding["carc"]] + primary_finding.get("rarc", []),
        root_cause=primary_finding["root_cause"],
        baseline_disposition=primary_disposition,
        required_evidence=evidence_needed,
        rationale=primary_finding["rationale"],
        filing_deadline=deadline_date,
        deadline_status=deadline_status
    )

    # Route Outcomes
    if primary_disposition == Disposition.MANUAL_REVIEW or triage_result.get("warnings"):
        route = RouteDecision(
            outcome=RouteOutcome.MANUAL_REVIEW,
            reason_code="BASELINE_WARNING_OR_MANUAL",
            routing_reason="Baseline hit a manual review state or generated warnings.",
            authorized_tools=[]
        )
    elif evidence_needed:
        route = RouteDecision(
            outcome=RouteOutcome.AGENT_REQUIRED,
            reason_code="EVIDENCE_MISSING",
            routing_reason="Evidence is required; routing to agent.",
            authorized_tools=["policy_retrieval", "encounter_retrieval"]
        )
    else:
        route = RouteDecision(
            outcome=RouteOutcome.DETERMINISTIC_ONLY,
            reason_code="BASELINE_RESOLVED",
            routing_reason="Baseline reached a conclusion without requiring external evidence.",
            authorized_tools=[]
        )

    return finding, route
