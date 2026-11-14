"""Run reviewed hosted-model comparisons with resumable local artifacts."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Optional, Sequence

from claim_agent.agent.hosted.factory import (
    HostedModelConfigurationError,
    HostedModelProfile,
    build_hosted_model_client,
)
from claim_agent.agent.model_interface import ModelClient
from claim_agent.agent.trace_writer import JsonLinesTraceWriter
from claim_agent.tools.knowledge_store import KnowledgeStore
from evaluation.hosted_comparison import (
    DEFAULT_HOSTED_COMPARISON_PLAN,
    HostedComparisonPlan,
    load_hosted_comparison_plan,
)
from evaluation.hosted_execution import (
    HostedCaseExecution,
    execute_hosted_case,
)
from evaluation.pilot_data import REPO_ROOT, Case, load_dataset
from evaluation.scripted_agent import prepare_evaluation_case

DEFAULT_OUTPUT_ROOT = REPO_ROOT / ".local-output" / "hosted-comparison"
KNOWLEDGE_ROOT = REPO_ROOT / "knowledge"


class HostedComparisonExecutionError(RuntimeError):
    """Report an unsafe, incomplete, or invalid comparison request."""


@dataclass(frozen=True)
class ComparisonSelection:
    """Reviewed subset of candidates, cases, and repetitions."""

    model_ids: tuple[str, ...]
    case_ids: tuple[str, ...]
    repeat_indices: tuple[int, ...]

    @property
    def run_count(self) -> int:
        """Return the number of selected candidate-case repetitions."""

        return (
            len(self.model_ids)
            * len(self.case_ids)
            * len(self.repeat_indices)
        )


ClientFactory = Callable[[HostedModelProfile], ModelClient]
CaseExecutor = Callable[..., HostedCaseExecution]


def select_comparison_runs(
    *,
    plan: HostedComparisonPlan,
    model_id: str | None = None,
    case_id: str | None = None,
    repeat_index: int | None = None,
) -> ComparisonSelection:
    """Validate optional filters against the frozen plan."""

    available_models = tuple(
        candidate.model_id for candidate in plan.candidates
    )
    available_cases = plan.case_ids
    available_repeats = tuple(range(1, plan.repetitions + 1))

    if model_id is not None and model_id not in available_models:
        raise HostedComparisonExecutionError(
            f"Model {model_id!r} is not in the frozen plan."
        )

    if case_id is not None and case_id not in available_cases:
        raise HostedComparisonExecutionError(
            f"Case {case_id!r} is not in the frozen plan."
        )

    if repeat_index is not None and repeat_index not in available_repeats:
        raise HostedComparisonExecutionError(
            f"Repeat {repeat_index!r} is not in the frozen plan."
        )

    return ComparisonSelection(
        model_ids=(
            (model_id,) if model_id is not None else available_models
        ),
        case_ids=(
            (case_id,) if case_id is not None else available_cases
        ),
        repeat_indices=(
            (repeat_index,) if repeat_index is not None else available_repeats
        ),
    )


def comparison_run_id(
    model_id: str,
    case_id: str,
    repeat_index: int,
) -> str:
    """Create a stable safe identifier for one planned repetition."""

    return (
        f"hosted-comparison-{model_id}-"
        f"{case_id}-r{repeat_index}"
    )


def comparison_run_directory(
    *,
    output_root: Path,
    plan_version: str,
    model_id: str,
    case_id: str,
    repeat_index: int,
) -> Path:
    """Return the dedicated artifact directory for one run."""

    return (
        output_root
        / plan_version
        / model_id
        / case_id
        / f"repeat-{repeat_index}"
    )


def _write_execution_artifacts(
    *,
    execution: HostedCaseExecution,
    run_directory: Path,
) -> None:
    """Write the output package, immutable trace, then completion marker."""

    run_directory.mkdir(parents=True, exist_ok=False)

    output_package_path = run_directory / "output-package.json"
    output_package_path.write_text(
        json.dumps(
            execution.output_package.model_dump(
                mode="json",
                exclude_none=True,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    trace_path = JsonLinesTraceWriter(run_directory / "trace").write(
        execution.canonical_trace_events
    )

    terminal_event = execution.canonical_trace_events[-1]
    completion = {
        "run_id": terminal_event.run_id,
        "case_id": terminal_event.case_id,
        "model_id": execution.canonical_trace_events[0].payload[
            "config"
        ]["model_id"],
        "terminal_event": terminal_event.event_type.value,
        "terminal_reason": (
            terminal_event.terminal_reason.value
            if terminal_event.terminal_reason is not None
            else None
        ),
        "final_disposition": (
            execution.output_package.final_disposition.value
        ),
        "trace_path": str(trace_path),
    }
    (run_directory / "complete.json").write_text(
        json.dumps(
            completion,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def run_selected_comparisons(
    *,
    plan: HostedComparisonPlan,
    output_root: Path,
    code_commit: str,
    selection: ComparisonSelection,
    confirm_live: bool,
    client_factory: ClientFactory = build_hosted_model_client,
    case_executor: CaseExecutor = execute_hosted_case,
) -> dict[str, object]:
    """Execute selected runs, skipping only completed artifacts."""

    if not confirm_live:
        raise HostedComparisonExecutionError(
            "Live comparison requires explicit confirmation."
        )

    dataset = load_dataset(plan.manifest_path())
    cases_by_id: dict[str, Case] = {
        case.case_id: case for case in dataset.cases
    }
    candidates_by_id = {
        candidate.model_id: candidate
        for candidate in plan.candidates
    }
    as_of = date.fromisoformat(dataset.as_of)
    knowledge_store = KnowledgeStore(KNOWLEDGE_ROOT)

    completed = 0
    skipped = 0

    for model_id in selection.model_ids:
        profile = candidates_by_id[model_id].hosted_profile()

        for case_id in selection.case_ids:
            prepared_case = prepare_evaluation_case(
                cases_by_id[case_id],
                as_of,
            )

            if not prepared_case.requires_agent:
                raise HostedComparisonExecutionError(
                    f"Planned case {case_id!r} does not require "
                    "agent execution."
                )

            for repeat_index in selection.repeat_indices:
                run_directory = comparison_run_directory(
                    output_root=output_root,
                    plan_version=plan.plan_version,
                    model_id=model_id,
                    case_id=case_id,
                    repeat_index=repeat_index,
                )
                completion_path = run_directory / "complete.json"

                if completion_path.is_file():
                    skipped += 1
                    continue

                if run_directory.exists():
                    raise HostedComparisonExecutionError(
                        "Incomplete run directory requires review: "
                        f"{run_directory}"
                    )

                model_client = client_factory(profile=profile)
                execution = case_executor(
                    prepared_case=prepared_case,
                    model_client=model_client,
                    profile=profile,
                    budgets=plan.budgets,
                    knowledge_store=knowledge_store,
                    run_id=comparison_run_id(
                        model_id,
                        case_id,
                        repeat_index,
                    ),
                    as_of=as_of,
                    code_commit=code_commit,
                    plan_version=plan.plan_version,
                    repeat_index=repeat_index,
                )
                _write_execution_artifacts(
                    execution=execution,
                    run_directory=run_directory,
                )
                completed += 1

    return {
        "plan_version": plan.plan_version,
        "selected_runs": selection.run_count,
        "completed_runs": completed,
        "skipped_completed_runs": skipped,
        "output_root": str(output_root / plan.plan_version),
    }


def main(arguments: Optional[Sequence[str]] = None) -> int:
    """Run an explicitly confirmed subset of the frozen comparison."""

    parser = argparse.ArgumentParser(
        description=(
            "Run the frozen development-only hosted-model "
            "comparison."
        )
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=DEFAULT_HOSTED_COMPARISON_PLAN,
    )
    parser.add_argument("--model-id")
    parser.add_argument("--case-id")
    parser.add_argument("--repeat-index", type=int)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )
    parser.add_argument(
        "--code-commit",
        required=True,
        help="Exact commit recorded in every run configuration.",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Confirm that selected runs may call Voyager.",
    )
    args = parser.parse_args(arguments)

    try:
        plan = load_hosted_comparison_plan(args.plan)
        selection = select_comparison_runs(
            plan=plan,
            model_id=args.model_id,
            case_id=args.case_id,
            repeat_index=args.repeat_index,
        )
        summary = run_selected_comparisons(
            plan=plan,
            output_root=args.output_root,
            code_commit=args.code_commit,
            selection=selection,
            confirm_live=args.confirm_live,
        )
    except (
        HostedComparisonExecutionError,
        HostedModelConfigurationError,
    ) as error:
        print(
            json.dumps(
                {
                    "error": "hosted_comparison_error",
                    "message": str(error),
                },
                sort_keys=True,
            )
        )
        return 2

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
