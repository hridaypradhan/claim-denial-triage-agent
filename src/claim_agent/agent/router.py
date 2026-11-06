"""Adapt frozen baseline findings into shared routing contracts."""

from datetime import date

from baseline.triage.claim import Claim
from baseline.triage.rules import triage_claim
from claim_agent.contracts import (
    BaselineFinding,
    Disposition,
    RootCause,
    RouteDecision,
    RouteOutcome,
)


EVIDENCE_INTERPRETATION_ROOT_CAUSES = frozenset(
    {
        RootCause.MEDICAL_NECESSITY,
        RootCause.BUNDLING_NCCI,
        RootCause.AUTHORIZATION,
    }
)

AGENT_EVIDENCE_TOOLS = (
    "retrieve_medical_policy",
    "get_encounter_note",
)


def analyze_and_route_claim(
    claim: Claim,
    as_of: date,
) -> list[tuple[BaselineFinding, RouteDecision]]:
    """Return one baseline finding and route for each denied service line.

    The frozen baseline remains responsible for deterministic claim analysis.
    This adapter preserves each service line's identity and translates the
    baseline output into the contracts consumed by the agent runtime.
    """

    triage_result = triage_claim(claim, as_of)
    routing_results: list[tuple[BaselineFinding, RouteDecision]] = []
    baseline_warnings = triage_result.get("warnings", [])

    # Evaluate every finding independently so multi-denial claims preserve
    # their service-line identities throughout routing.
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

        required_evidence = finding_data.get("evidence_needed", [])

        # Preserve the adjustment group because CO and PR codes can lead to
        # different responsibility and safety outcomes.
        group_code = finding_data.get("group_code", "CO")
        primary_denial_code = f"{group_code}-{finding_data['carc']}"
        denial_codes = [primary_denial_code] + finding_data.get("rarc", [])

        finding = BaselineFinding(
            contract_version="0.1",
            claim_id=triage_result["claim_id"],
            service_line_id=finding_data["service_line"],
            denial_codes=denial_codes,
            root_cause=root_cause,
            baseline_disposition=disposition,
            required_evidence=required_evidence,
            rationale=finding_data["rationale"],
            filing_deadline=deadline_date,
            deadline_status=deadline_status,
        )

        if disposition is Disposition.MANUAL_REVIEW:
            route = RouteDecision(
                contract_version="0.1",
                claim_id=triage_result["claim_id"],
                service_line_id=finding_data["service_line"],
                outcome=RouteOutcome.MANUAL_REVIEW,
                reason_code="BASELINE_MANUAL_REVIEW",
                routing_reason="Baseline hit a manual review state.",
                authorized_tools=[],
            )
        elif baseline_warnings:
            route = RouteDecision(
                contract_version="0.1",
                claim_id=triage_result["claim_id"],
                service_line_id=finding_data["service_line"],
                outcome=RouteOutcome.MANUAL_REVIEW,
                reason_code="BASELINE_WARNING",
                routing_reason="Baseline generated warnings.",
                authorized_tools=[],
            )
        elif (
            disposition is Disposition.APPEAL_WITH_DOCUMENTATION
            and root_cause in EVIDENCE_INTERPRETATION_ROOT_CAUSES
        ):
            # Only appeal cases requiring evidence interpretation enter the
            # agent. Deterministic corrections remain in the baseline flow.
            route = RouteDecision(
                contract_version="0.1",
                claim_id=triage_result["claim_id"],
                service_line_id=finding_data["service_line"],
                outcome=RouteOutcome.AGENT_REQUIRED,
                reason_code="EVIDENCE_EVALUATION_REQUIRED",
                routing_reason=(
                    "Agent must interpret evidence for this root cause."
                ),
                authorized_tools=list(AGENT_EVIDENCE_TOOLS),
            )
        else:
            route = RouteDecision(
                contract_version="0.1",
                claim_id=triage_result["claim_id"],
                service_line_id=finding_data["service_line"],
                outcome=RouteOutcome.DETERMINISTIC_ONLY,
                reason_code="BASELINE_RESOLVED",
                routing_reason=(
                    "Baseline reached a conclusion without requiring agent "
                    "interpretation."
                ),
                authorized_tools=[],
            )

        routing_results.append((finding, route))

    return routing_results
