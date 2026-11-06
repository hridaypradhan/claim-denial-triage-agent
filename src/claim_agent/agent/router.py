from datetime import date
from typing import Tuple, List

from baseline.triage.claim import Claim
from baseline.triage.rules import triage_claim

# Fix: Import directly from the public boundary we just merged!
from claim_agent.contracts import (
    BaselineFinding, 
    RouteDecision, 
    Disposition,
    RootCause,
    RouteOutcome
)

def analyze_and_route_claim(claim: Claim, as_of: date) -> List[Tuple[BaselineFinding, RouteDecision]]:
    """
    Wraps the frozen deterministic baseline. Evaluates the Claim object 
    and outputs deterministic routing contracts per service line.
    """
    triage_result = triage_claim(claim, as_of)
    results = []
    warnings = triage_result.get("warnings", [])
    
    # Fix 2: Iterate over all findings to support multi-denial claims
    for finding_data in triage_result["findings"]:
        disposition = Disposition(finding_data["disposition"])
        root_cause = RootCause(finding_data["root_cause"])
        
        raw_deadline = finding_data.get("deadline")
        deadline_date = date.fromisoformat(raw_deadline) if raw_deadline else None
        
        days_remaining = finding_data.get("days_remaining")
        if days_remaining is None:
            deadline_status = "NONE"
        elif days_remaining < 0:
            deadline_status = "EXPIRED"
        else:
            deadline_status = "ACTIVE"
            
        evidence_needed = finding_data.get("evidence_needed", [])
        
        # Fix 4: Prepend the group code to the CARC (e.g., 'CO-45')
        group_code = finding_data.get("group_code", "CO")
        primary_code = f"{group_code}-{finding_data['carc']}"
        denial_codes = [primary_code] + finding_data.get("rarc", [])

        finding = BaselineFinding(
            contract_version="0.1",
            claim_id=triage_result["claim_id"],
            service_line_id=finding_data["service_line"],
            denial_codes=denial_codes,
            root_cause=root_cause,
            baseline_disposition=disposition,
            required_evidence=evidence_needed,
            rationale=finding_data["rationale"],
            filing_deadline=deadline_date,
            deadline_status=deadline_status
        )

        # Fix 1 & 6: Route based on whether the agent needs to interpret evidence, and separate manual reasons
        if disposition == Disposition.MANUAL_REVIEW:
            route = RouteDecision(
                contract_version="0.1",
                claim_id=triage_result["claim_id"],
                service_line_id=finding_data["service_line"],
                outcome=RouteOutcome.MANUAL_REVIEW,
                reason_code="BASELINE_MANUAL_REVIEW",
                routing_reason="Baseline hit a manual review state.",
                authorized_tools=[]
            )
        elif warnings:
            route = RouteDecision(
                contract_version="0.1",
                claim_id=triage_result["claim_id"],
                service_line_id=finding_data["service_line"],
                outcome=RouteOutcome.MANUAL_REVIEW,
                reason_code="BASELINE_WARNING",
                routing_reason="Baseline generated warnings.",
                authorized_tools=[]
            )
        elif root_cause in (RootCause.MEDICAL_NECESSITY, RootCause.BUNDLING_NCCI, RootCause.AUTHORIZATION):
            route = RouteDecision(
                contract_version="0.1",
                claim_id=triage_result["claim_id"],
                service_line_id=finding_data["service_line"],
                outcome=RouteOutcome.AGENT_REQUIRED,
                reason_code="EVIDENCE_EVALUATION_REQUIRED",
                routing_reason="Agent must interpret evidence for this root cause.",
                # Fix 3: Use the exact tool names matching the manifest
                authorized_tools=["retrieve_medical_policy", "get_encounter_note"]
            )
        else:
            route = RouteDecision(
                contract_version="0.1",
                claim_id=triage_result["claim_id"],
                service_line_id=finding_data["service_line"],
                outcome=RouteOutcome.DETERMINISTIC_ONLY,
                reason_code="BASELINE_RESOLVED",
                routing_reason="Baseline reached a conclusion without requiring agent interpretation.",
                authorized_tools=[]
            )
            
        results.append((finding, route))

    return results