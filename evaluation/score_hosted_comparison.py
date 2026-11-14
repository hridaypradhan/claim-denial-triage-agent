"""Score completed hosted comparison artifacts without making model calls."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from claim_agent.agent.canonical_trace import TraceEvent
from claim_agent.contracts import OutputPackage
from evaluation.hosted_comparison import (
    DEFAULT_HOSTED_COMPARISON_PLAN,
    HostedComparisonPlan,
    load_hosted_comparison_plan,
)
from evaluation.metrics import safety_errors
from evaluation.pilot_data import (
    Case,
    Label,
    load_dataset,
    load_labels,
)
from evaluation.run_hosted_comparison import (
    DEFAULT_OUTPUT_ROOT,
    comparison_run_directory,
)
from evaluation.run_pilot import case_facts
from evaluation.scripted_agent import prepare_evaluation_case
from evaluation.systems import prediction_from_output_package
from evaluation.trace_metrics import (
    ClaimService,
    measure_traces,
    trace_safety_counts,
)


class HostedComparisonScoringError(RuntimeError):
    """Report incomplete or inconsistent comparison artifacts."""


@dataclass(frozen=True)
class CompletedRun:
    """Loaded output and trace for one planned repetition."""

    model_id: str
    case: Case
    repeat_index: int
    output_package: OutputPackage
    trace_events: tuple[TraceEvent, ...]


def _load_trace(path: Path) -> tuple[TraceEvent, ...]:
    """Load one canonical JSON Lines trace."""

    return tuple(
        TraceEvent.model_validate_json(line)
        for line in path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    )


def load_completed_runs(
    *,
    plan: HostedComparisonPlan,
    output_root: Path,
) -> tuple[list[CompletedRun], list[str]]:
    """Load every complete artifact and identify missing planned runs."""

    dataset = load_dataset(plan.manifest_path())
    cases_by_id = {
        case.case_id: case for case in dataset.cases
    }
    completed: list[CompletedRun] = []
    missing: list[str] = []

    for candidate in plan.candidates:
        for case_id in plan.case_ids:
            for repeat_index in range(1, plan.repetitions + 1):
                run_directory = comparison_run_directory(
                    output_root=output_root,
                    plan_version=plan.plan_version,
                    model_id=candidate.model_id,
                    case_id=case_id,
                    repeat_index=repeat_index,
                )
                completion_path = run_directory / "complete.json"

                if not completion_path.is_file():
                    missing.append(
                        f"{candidate.model_id}/{case_id}/"
                        f"repeat-{repeat_index}"
                    )
                    continue

                package = OutputPackage.model_validate_json(
                    (
                        run_directory / "output-package.json"
                    ).read_text(encoding="utf-8")
                )
                trace_paths = list(
                    (run_directory / "trace").glob("*.jsonl")
                )
                if len(trace_paths) != 1:
                    raise HostedComparisonScoringError(
                        "Expected one trace in "
                        f"{run_directory}, found {len(trace_paths)}."
                    )

                completed.append(
                    CompletedRun(
                        model_id=candidate.model_id,
                        case=cases_by_id[case_id],
                        repeat_index=repeat_index,
                        output_package=package,
                        trace_events=_load_trace(trace_paths[0]),
                    )
                )

    return completed, missing


def _ratio(hits: int, total: int) -> dict[str, object]:
    """Represent a rate with its inspectable numerator and denominator."""

    return {
        "hits": hits,
        "total": total,
        "value": None if total == 0 else hits / total,
    }


def _criterion_evidence_support(
    output_package: OutputPackage,
    label: Label,
) -> tuple[int, int]:
    """Count criteria grounded in evidence adjudicated for that criterion.

    A criterion is supported only when the predicted status matches and at
    least one citation belongs to that criterion's accepted passage set. This
    allows alternative adjudicated passages without treating any retrieved
    citation as semantically interchangeable evidence.
    """

    if not label.criteria:
        return 0, 0

    decision = output_package.proposed_decision
    predicted_by_id = {
        assessment.criterion_id: assessment
        for assessment in (
            decision.criterion_assessments if decision is not None else []
        )
    }
    hits = 0
    for expected in label.criteria:
        predicted = predicted_by_id.get(expected.criterion_id)
        if predicted is None or predicted.status.value != expected.status:
            continue
        if set(predicted.citation_ids).intersection(expected.passage_ids):
            hits += 1
    return hits, len(label.criteria)


def score_completed_runs(
    *,
    plan: HostedComparisonPlan,
    completed_runs: Sequence[CompletedRun],
) -> dict[str, dict[str, object]]:
    """Aggregate the reviewed ranking metrics by candidate model."""

    dataset = load_dataset(plan.manifest_path())
    labels = load_labels(plan.labels_path(), dataset)
    facts_by_case_id = case_facts(dataset)
    as_of = date.fromisoformat(dataset.as_of)
    results: dict[str, dict[str, object]] = {}

    for candidate in plan.candidates:
        model_runs = [
            run
            for run in completed_runs
            if run.model_id == candidate.model_id
        ]
        joint_hits = 0
        manual_review_hits = 0
        output_safety = Counter()
        outcome_by_case: dict[
            str,
            set[tuple[str, str, str | None]],
        ] = defaultdict(set)
        traces = {}
        services = {}
        valid_actions = 0
        model_responses = 0
        provider_failures = 0
        criterion_evidence_hits = 0
        criterion_evidence_total = 0
        elapsed_values: list[int] = []
        input_tokens = 0
        output_tokens = 0

        for run in model_runs:
            run_key = f"{run.case.case_id}-r{run.repeat_index}"
            prediction = prediction_from_output_package(
                case=run.case,
                system=candidate.model_id,
                output_package=run.output_package,
                trace_events=run.trace_events,
            )
            label: Label = labels[run.case.case_id]

            run_evidence_hits, run_evidence_total = (
                _criterion_evidence_support(
                    run.output_package,
                    label,
                )
            )
            criterion_evidence_hits += run_evidence_hits
            criterion_evidence_total += run_evidence_total

            if (
                prediction.root_cause == label.root_cause
                and prediction.disposition == label.disposition
            ):
                joint_hits += 1

            if (
                prediction.disposition == "MANUAL_REVIEW"
            ) == label.requires_manual_review:
                manual_review_hits += 1

            prepared_case = prepare_evaluation_case(
                run.case,
                as_of,
            )
            claim_context = prepared_case.claim_context
            if claim_context is None:
                raise HostedComparisonScoringError(
                    f"{run.case.case_id} has no claim context."
                )

            services[run_key] = ClaimService(
                payer=claim_context.payer,
                procedure_code=claim_context.procedure_code,
                service_date=claim_context.service_date.isoformat(),
            )
            trace_dicts = [
                event.model_dump(
                    mode="json",
                    exclude_none=True,
                )
                for event in run.trace_events
            ]
            traces[run_key] = trace_dicts

            output_safety.update(
                safety_errors(
                    prediction,
                    label,
                    facts_by_case_id[run.case.case_id],
                    as_of,
                )
            )

            rejected_request_ids = {
                event.request_id
                for event in run.trace_events
                if event.event_type.value == "MODEL_OUTPUT_REJECTED"
            }
            response_events = [
                event
                for event in run.trace_events
                if event.event_type.value == "MODEL_RESPONDED"
            ]
            model_responses += len(response_events)
            valid_actions += sum(
                event.request_id not in rejected_request_ids
                for event in response_events
            )

            for event in run.trace_events:
                if event.event_type.value == "MODEL_FAILED":
                    provider_failures += 1
                if (
                    event.event_type.value
                    in {"MODEL_RESPONDED", "MODEL_FAILED"}
                    and event.elapsed_ms is not None
                ):
                    elapsed_values.append(event.elapsed_ms)
                if event.input_tokens is not None:
                    input_tokens += event.input_tokens
                if event.output_tokens is not None:
                    output_tokens += event.output_tokens

            terminal = run.trace_events[-1]
            outcome_by_case[run.case.case_id].add(
                (
                    prediction.root_cause,
                    prediction.disposition,
                    (
                        terminal.terminal_reason.value
                        if terminal.terminal_reason is not None
                        else None
                    ),
                )
            )

        trace_metrics = measure_traces(
            traces=traces,
            services=services,
        )
        trace_safety = trace_safety_counts(trace_metrics)
        critical_safety_failures = (
            sum(output_safety.values())
            + sum(trace_safety.values())
        )
        stable_cases = sum(
            len(outcomes) == 1
            for outcomes in outcome_by_case.values()
        )
        total_cases_with_runs = len(outcome_by_case)

        results[candidate.model_id] = {
            "completed_runs": len(model_runs),
            "critical_safety_failures": critical_safety_failures,
            "output_safety_counts": dict(output_safety),
            "trace_safety_counts": trace_safety,
            "valid_action_schema_rate": _ratio(
                valid_actions,
                model_responses,
            ),
            "joint_accuracy": _ratio(
                joint_hits,
                len(model_runs),
            ),
            "criterion_evidence_support_rate": _ratio(
                criterion_evidence_hits,
                criterion_evidence_total,
            ),
            "correct_manual_review_rate": _ratio(
                manual_review_hits,
                len(model_runs),
            ),
            "valid_tool_call_rate": _ratio(
                trace_metrics.tool_calls_valid,
                trace_metrics.tool_calls,
            ),
            "correct_policy_request_rate": _ratio(
                trace_metrics.policy_requests_matching_claim,
                trace_metrics.policy_requests,
            ),
            "citation_precision": _ratio(
                trace_metrics.citations_retrieved,
                trace_metrics.citations,
            ),
            "stability_rate": _ratio(
                stable_cases,
                total_cases_with_runs,
            ),
            "provider_failures": provider_failures,
            "mean_model_elapsed_ms": (
                None
                if not elapsed_values
                else sum(elapsed_values) / len(elapsed_values)
            ),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    return results

def _rate_value(
    metrics: dict[str, object],
    name: str,
) -> float:
    value = metrics[name]["value"]
    return -1.0 if value is None else float(value)


def rank_candidates(
    scores: dict[str, dict[str, object]],
) -> list[str]:
    """Apply the reviewed lexicographic ranking and tie breakers."""

    return sorted(
        scores,
        key=lambda model_id: (
            scores[model_id]["critical_safety_failures"],
            -_rate_value(
                scores[model_id],
                "valid_action_schema_rate",
            ),
            -_rate_value(
                scores[model_id],
                "correct_policy_request_rate",
            ),
            -_rate_value(scores[model_id], "joint_accuracy"),
            -_rate_value(
                scores[model_id],
                "criterion_evidence_support_rate",
            ),
            -_rate_value(
                scores[model_id],
                "citation_precision",
            ),
            -_rate_value(
                scores[model_id],
                "correct_manual_review_rate",
            ),
            -_rate_value(scores[model_id], "stability_rate"),
            scores[model_id]["provider_failures"],
            (
                scores[model_id]["mean_model_elapsed_ms"]
                if scores[model_id]["mean_model_elapsed_ms"] is not None
                else float("inf")
            ),
            (
                scores[model_id]["input_tokens"]
                + scores[model_id]["output_tokens"]
            ),
        ),
    )


def score_comparison(
    *,
    plan: HostedComparisonPlan,
    output_root: Path,
    allow_partial: bool,
) -> dict[str, object]:
    """Score artifacts and name a winner only for a complete plan."""

    completed_runs, missing_runs = load_completed_runs(
        plan=plan,
        output_root=output_root,
    )
    if missing_runs and not allow_partial:
        raise HostedComparisonScoringError(
            f"Comparison is incomplete: {len(missing_runs)} "
            "planned runs are missing."
        )

    scores = score_completed_runs(
        plan=plan,
        completed_runs=completed_runs,
    )
    ranking = rank_candidates(scores)
    complete = not missing_runs

    return {
        "plan_version": plan.plan_version,
        "complete": complete,
        "completed_runs": len(completed_runs),
        "planned_runs": (
            len(plan.candidates)
            * len(plan.case_ids)
            * plan.repetitions
        ),
        "missing_runs": missing_runs,
        "scores": scores,
        "ranking": ranking,
        "winner": ranking[0] if complete else None,
    }


def main(arguments: Optional[Sequence[str]] = None) -> int:
    """Score saved artifacts without contacting Voyager."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--plan",
        type=Path,
        default=DEFAULT_HOSTED_COMPARISON_PLAN,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
    )
    args = parser.parse_args(arguments)

    try:
        plan = load_hosted_comparison_plan(args.plan)
        report = score_comparison(
            plan=plan,
            output_root=args.output_root,
            allow_partial=args.allow_partial,
        )
    except HostedComparisonScoringError as error:
        print(
            json.dumps(
                {
                    "error": "hosted_comparison_scoring_error",
                    "message": str(error),
                },
                sort_keys=True,
            )
        )
        return 2

    report_directory = args.output_root / plan.plan_version
    report_directory.mkdir(parents=True, exist_ok=True)
    report_path = report_directory / "comparison.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
