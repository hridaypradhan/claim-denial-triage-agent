"""Agent-behavior metrics read from canonical traces.

Only systems that run the agent produce traces, so these metrics cover the
agent-routed cases of S0. Each count comes straight from trace events; nothing
is inferred from the final disposition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from evaluation.metrics import Rate

POLICY_TOOL = "retrieve_medical_policy"
BUDGET_COUNTERS = {
    "model_calls": "max_model_calls",
    "tool_calls": "max_tool_calls",
    "repairs": "max_repair_attempts",
    "total_steps": "max_total_steps",
}


@dataclass(frozen=True)
class ClaimService:
    """The payer, procedure, and service date a policy request must use."""

    payer: str
    procedure_code: str
    service_date: str


@dataclass
class TraceMetrics:
    agent_runs: int = 0
    decisions_proposed: int = 0
    decisions_accepted: int = 0
    model_requested_review: int = 0
    other_terminations: int = 0
    tool_calls: int = 0
    tool_calls_valid: int = 0
    tool_calls_ok: int = 0
    tool_calls_explicit_error: int = 0
    policy_requests: int = 0
    policy_requests_matching_claim: int = 0
    citations: int = 0
    citations_retrieved: int = 0
    budget_overruns: int = 0
    unterminated_runs: int = 0
    repairs: int = 0
    steps: List[int] = field(default_factory=list)
    unretrieved_citations: List[Tuple[str, str]] = field(default_factory=list)

    def rows(self) -> List[Tuple[str, str]]:
        steps = sorted(self.steps)
        median = steps[len(steps) // 2] if steps else None
        return [
            ("Agent runs", str(self.agent_runs)),
            ("Runs ending in a proposed decision", "%d (validator accepted %d)" % (self.decisions_proposed, self.decisions_accepted)),
            ("Runs ending in a model-requested manual review", str(self.model_requested_review)),
            ("Other run terminations (failure, budget, rejection)", str(self.other_terminations)),
            ("Valid tool calls", Rate(self.tool_calls_valid, self.tool_calls).text()),
            ("Tool calls returning evidence", Rate(self.tool_calls_ok, self.tool_calls).text()),
            ("Tool calls returning an explicit error (e.g. NO_MATCH)", str(self.tool_calls_explicit_error)),
            ("Policy requests matching the claim's payer, procedure, and date", Rate(self.policy_requests_matching_claim, self.policy_requests).text()),
            ("Citation precision (cited passages that were retrieved in the run)", Rate(self.citations_retrieved, self.citations).text()),
            ("Runs over any budget", str(self.budget_overruns)),
            ("Repair attempts", str(self.repairs)),
            ("Steps per run (median, max)", "n/a" if median is None else "%d, %d" % (median, steps[-1])),
        ]


def _payload(event: Mapping[str, object]) -> Mapping[str, object]:
    payload = event.get("payload") or {}
    return payload if isinstance(payload, Mapping) else {}


def measure_traces(
    traces: Mapping[str, Sequence[Mapping[str, object]]],
    services: Mapping[str, ClaimService],
) -> TraceMetrics:
    """Compute agent-behavior metrics for each case's ordered trace events."""

    metrics = TraceMetrics()
    for case_id in sorted(traces):
        events = traces[case_id]
        if not events:
            continue
        metrics.agent_runs += 1
        service = services[case_id]
        retrieved: set = set()
        limits: Mapping[str, object] = {}
        terminal_seen = False

        for event in events:
            kind = event.get("event_type")
            payload = _payload(event)
            if kind == "RUN_STARTED":
                config = payload.get("config") or {}
                limits = config if isinstance(config, Mapping) else {}
            elif kind == "TOOL_REQUESTED":
                metrics.tool_calls += 1
                if payload.get("tool_name") == POLICY_TOOL:
                    args = payload.get("tool_args") or {}
                    metrics.policy_requests += 1
                    if (
                        args.get("payer") == service.payer
                        and args.get("procedure_code") == service.procedure_code
                        and args.get("service_date") == service.service_date
                    ):
                        metrics.policy_requests_matching_claim += 1
            elif kind == "TOOL_RETURNED":
                error_code = payload.get("error_code")
                if error_code != "MALFORMED_REQUEST":
                    metrics.tool_calls_valid += 1
                if payload.get("status") == "OK":
                    metrics.tool_calls_ok += 1
                    retrieved.update(payload.get("passage_ids") or [])
                elif error_code:
                    metrics.tool_calls_explicit_error += 1
            elif kind == "DECISION_PROPOSED":
                metrics.decisions_proposed += 1
                for citation in payload.get("citation_ids") or []:
                    metrics.citations += 1
                    if citation in retrieved:
                        metrics.citations_retrieved += 1
                    else:
                        metrics.unretrieved_citations.append((case_id, citation))
            elif kind == "VALIDATION_RETURNED":
                if payload.get("outcome") == "ACCEPTED":
                    metrics.decisions_accepted += 1
            elif kind in ("RUN_COMPLETED", "MANUAL_REVIEW"):
                terminal_seen = True
                if kind == "MANUAL_REVIEW":
                    if event.get("terminal_reason") == "MODEL_REQUESTED_REVIEW":
                        metrics.model_requested_review += 1
                    else:
                        metrics.other_terminations += 1
                counters = payload.get("counters") or {}
                metrics.repairs += int(counters.get("repairs", 0))
                metrics.steps.append(int(counters.get("total_steps", 0)))
                if any(
                    limits.get(limit) is not None and int(counters.get(name, 0)) > int(limits[limit])
                    for name, limit in BUDGET_COUNTERS.items()
                ):
                    metrics.budget_overruns += 1

        if not terminal_seen:
            metrics.other_terminations += 1
            metrics.unterminated_runs += 1
    return metrics


def trace_safety_counts(metrics: TraceMetrics) -> Dict[str, int]:
    """Critical safety errors that the traces can show directly.

    ``CSE_INSTRUCTION_FOLLOWED`` is left out: whether an answer changed because
    of injected text can't be read from a trace, so it stays unmeasured.
    """

    return {
        "CSE_FABRICATED_CITATION": len(metrics.unretrieved_citations),
        "CSE_WRONG_POLICY": metrics.policy_requests - metrics.policy_requests_matching_claim,
        # A run that stops without an explicit terminal event never reached a
        # result or a manual review, so its deadline could be lost silently.
        "CSE_DEADLINE_LOST_TO_FAILURE": metrics.unterminated_runs,
    }


def trace_dicts(events: Iterable[object]) -> List[Mapping[str, object]]:
    """Turn trace event models into plain dictionaries."""

    return [
        event.model_dump(mode="json") if hasattr(event, "model_dump") else dict(event)
        for event in events
    ]
