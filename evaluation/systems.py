"""Compared systems for the Milestone 2 pilot evaluation.

Each system turns one denied service-line case into a ``Prediction``. S1 runs
the frozen baseline. S0 runs the current deterministic router and invokes the
bounded scripted agent only for agent-authorized cases. S2 runs the same router
but sends every agent-authorized case to manual review instead of the agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from baseline.triage.claim import load_claim
from baseline.triage.rules import triage_claim

from claim_agent.agent.canonical_trace import TraceEvent
from claim_agent.contracts import Disposition, OutputPackage, RouteOutcome
from claim_agent.output.package import create_output_package
from claim_agent.tools.knowledge_store import KnowledgeStore
from evaluation.dataset_a_scripts import DATASET_A_SCRIPTED_PLANS
from evaluation.dataset_b_scripts import DATASET_B_SCRIPTED_PLANS
from evaluation.pilot_data import REPO_ROOT, Case
from evaluation.scripted_agent import (
    build_non_agent_output_package,
    execute_scripted_agent_case,
    prepare_evaluation_case,
    scripted_plan_for_case,
)


class PredictionError(RuntimeError):
    """Raised when a system cannot produce one prediction for a case."""


@dataclass(frozen=True)
class Prediction:
    case_id: str
    system: str
    disposition: str
    root_cause: str
    deadline: Optional[str]
    approval_state: Optional[str] = None
    warnings: Tuple[str, ...] = ()
    details: Dict[str, object] = field(default_factory=dict)
    output_package: Optional[OutputPackage] = None
    trace_events: Tuple[TraceEvent, ...] = ()


BASELINE_V1 = "baseline-v1"

SCRIPTED_AGENT = "scripted-agent"
ESCALATE_ALL = "escalate-all"
ESCALATE_ALL_REASON = "ESCALATE_ALL_BASELINE"
KNOWLEDGE_ROOT = REPO_ROOT / "knowledge"
PACKAGE_VERSION = "v1"

DEADLINE_BEARING_DISPOSITIONS = {
    Disposition.APPEAL_WITH_DOCUMENTATION,
    Disposition.CORRECT_AND_RESUBMIT,
    Disposition.ROUTE_TO_CORRECT_PAYER,
}

SCRIPTED_PLANS = {
    **DATASET_A_SCRIPTED_PLANS,
    **DATASET_B_SCRIPTED_PLANS,
}


def predict_baseline_v1(case: Case, as_of: date) -> Prediction:
    """Run the unchanged rule-based baseline and keep the finding for this line.

    The baseline returns one finding per denial. A case is one service line,
    so a line with more than one denial is rejected rather than merged by a
    rule the rubric does not define.
    """

    claim = load_claim(case.claim_path)
    if claim.claim_id != case.claim_id:
        raise PredictionError(
            "%s: claim file holds %s" % (case.case_id, claim.claim_id)
        )

    result = triage_claim(claim, as_of)
    findings: List[dict] = [
        f for f in result["findings"] if f["service_line"] == case.service_line_id
    ]
    if len(findings) != 1:
        raise PredictionError(
            "%s: expected one baseline finding for the line, got %d"
            % (case.case_id, len(findings))
        )
    finding = findings[0]
    return Prediction(
        case_id=case.case_id,
        system=BASELINE_V1,
        disposition=finding["disposition"],
        root_cause=finding["root_cause"],
        deadline=finding["deadline"],
        approval_state=None,  # baseline-v1 has no approval state; reported as n/a
        warnings=tuple(result["warnings"]),
        details={
            "carc": finding["carc"],
            "group_code": finding["group_code"],
            "confidence": finding["confidence"],
        },
    )


def predict_scripted_agent(case: Case, as_of: date) -> Prediction:
    """Run one case through rules and, only when authorized, the agent runtime."""

    prepared_case = prepare_evaluation_case(case, as_of)
    knowledge_store = KnowledgeStore(KNOWLEDGE_ROOT)
    run_id = f"{case.case_id}-{SCRIPTED_AGENT}"

    if prepared_case.requires_agent:
        plan = scripted_plan_for_case(
            case_id=case.case_id,
            plans_by_case_id=SCRIPTED_PLANS,
        )
        execution = execute_scripted_agent_case(
            prepared_case=prepared_case,
            plan=plan,
            knowledge_store=knowledge_store,
            run_id=run_id,
            as_of=as_of,
        )
        output_package = execution.output_package
        trace_events = execution.canonical_trace_events
    else:
        output_package = build_non_agent_output_package(
            prepared_case=prepared_case,
            run_id=run_id,
            package_version=PACKAGE_VERSION,
            knowledge_snapshot_id=knowledge_store.snapshot_id,
        )
        trace_events = ()

    return _prediction_from_package(
        case=case,
        system=SCRIPTED_AGENT,
        output_package=output_package,
        trace_events=trace_events,
    )


def predict_escalate_all(case: Case, as_of: date) -> Prediction:
    """Run the router, then escalate every agent-authorized case to a person.

    Rule-decidable and router manual-review cases are packaged exactly as in
    S0, so S0 and S2 differ only on the cases the agent would have handled.
    """

    prepared_case = prepare_evaluation_case(case, as_of)
    knowledge_store = KnowledgeStore(KNOWLEDGE_ROOT)
    run_id = f"{case.case_id}-{ESCALATE_ALL}"

    if prepared_case.requires_agent:
        output_package = create_output_package(
            run_id=run_id,
            package_version=PACKAGE_VERSION,
            knowledge_snapshot_id=knowledge_store.snapshot_id,
            finding=prepared_case.baseline_finding,
            route_outcome=RouteOutcome.AGENT_REQUIRED,
            agent_terminal_reason=ESCALATE_ALL_REASON,
        )
    else:
        output_package = build_non_agent_output_package(
            prepared_case=prepared_case,
            run_id=run_id,
            package_version=PACKAGE_VERSION,
            knowledge_snapshot_id=knowledge_store.snapshot_id,
        )

    return _prediction_from_package(
        case=case,
        system=ESCALATE_ALL,
        output_package=output_package,
        trace_events=(),
    )


def _prediction_from_package(
    case: Case,
    system: str,
    output_package: OutputPackage,
    trace_events: Tuple[TraceEvent, ...],
) -> Prediction:
    """Convert one output package into the scored prediction fields."""

    # Only dispositions that authorize a time-bounded follow-up action expose
    # the baseline filing deadline in evaluation results.
    filing_deadline = (
        output_package.baseline_finding.filing_deadline
        if output_package.final_disposition
        in DEADLINE_BEARING_DISPOSITIONS
        else None
    )

    return Prediction(
        case_id=case.case_id,
        system=system,
        disposition=output_package.final_disposition.value,
        root_cause=output_package.final_root_cause.value,
        deadline=(filing_deadline.isoformat() if filing_deadline is not None else None),
        approval_state="PENDING_REVIEW",
        details={
            "route_outcome": output_package.route_outcome.value,
            "manual_review_reasons": list(output_package.manual_review_reasons),
            "agent_terminal_reason": (output_package.agent_terminal_reason),
        },
        output_package=output_package,
        trace_events=trace_events,
    )


SYSTEMS = {
    BASELINE_V1: predict_baseline_v1,
    SCRIPTED_AGENT: predict_scripted_agent,
    ESCALATE_ALL: predict_escalate_all,
}
