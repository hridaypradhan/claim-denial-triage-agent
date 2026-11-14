"""Run a production-shaped demonstration using the local knowledge corpus.

Unlike the smaller offline demonstration, this runner uses the merged baseline,
routing, tool-dispatch, evidence, and validation contracts. The model remains
scripted so the demonstration is deterministic and requires no credentials.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping, Optional, Sequence

from claim_agent.agent.integrated_demo_setup import (
    DEMO_AS_OF,
    DEMO_BUDGET_LIMITS,
    DEMO_CODE_COMMIT,
    DEMO_PACKAGE_VERSION,
    DEMO_RUN_ID,
    ENCOUNTER_PASSAGE_IDS,
    KNOWLEDGE_ROOT,
    POLICY_PASSAGE_IDS,
    build_demo_baseline_finding,
    build_demo_claim_context,
    build_demo_dispatcher,
    build_demo_model,
    build_demo_route_decision,
    build_demo_run_configuration,
)
from claim_agent.agent.integrated_runtime import run_integrated_case
from claim_agent.agent.planning_prompt import (
    PLANNING_SYSTEM_INSTRUCTIONS,
)
from claim_agent.agent.trace_adapter import CanonicalTraceAdapter
from claim_agent.agent.trace_writer import JsonLinesTraceWriter
from claim_agent.tools.knowledge_store import KnowledgeStore


def run_integrated_demo(
    trace_output_directory: Path | None = None,
    code_commit: str = DEMO_CODE_COMMIT,
) -> Mapping[str, object]:
    """Run the routed case and optionally persist its canonical audit trace."""

    knowledge_store = KnowledgeStore(KNOWLEDGE_ROOT)
    route_decision = build_demo_route_decision()
    run_configuration = build_demo_run_configuration(
        knowledge_snapshot_id=knowledge_store.snapshot_id,
        code_commit=code_commit,
    )

    integrated_result = run_integrated_case(
        run_id=DEMO_RUN_ID,
        system_instructions=PLANNING_SYSTEM_INSTRUCTIONS,
        model_client=build_demo_model(),
        dispatcher=build_demo_dispatcher(knowledge_store),
        baseline_finding=build_demo_baseline_finding(),
        claim_context=build_demo_claim_context(),
        route_decision=route_decision,
        budget_limits=DEMO_BUDGET_LIMITS,
        as_of=DEMO_AS_OF,
        package_version=DEMO_PACKAGE_VERSION,
        knowledge_snapshot_id=knowledge_store.snapshot_id,
    )

    runtime_result = integrated_result.runtime_result
    canonical_trace_events = CanonicalTraceAdapter(
        run_configuration=run_configuration,
        route_decision=route_decision,
    ).adapt(runtime_result.trace_entries)
    trace_path = None
    if trace_output_directory is not None:
        trace_path = JsonLinesTraceWriter(trace_output_directory).write(
            canonical_trace_events
        )

    return {
        "run_id": runtime_result.final_state.run_id,
        "phase": runtime_result.final_state.phase.value,
        "terminal_reason": runtime_result.final_state.terminal_reason,
        "validated_decision": (
            dict(runtime_result.validated_decision)
            if runtime_result.validated_decision is not None
            else None
        ),
        "output_package": integrated_result.output_package.model_dump(mode="json"),
        "budget_usage": {
            "model_calls": runtime_result.budget_usage.model_calls_used,
            "tool_calls": runtime_result.budget_usage.tool_calls_used,
            "repair_attempts": (runtime_result.budget_usage.repair_attempts_used),
            "total_steps": runtime_result.budget_usage.total_steps_used,
        },
        "retrieved_passage_ids": [
            passage.passage_id for passage in integrated_result.retrieved_evidence
        ],
        "policy_formula": integrated_result.policy_formula,
        "trace_events": [
            trace_entry.event_name for trace_entry in runtime_result.trace_entries
        ],
        "canonical_trace_events": [
            event.event_type.value for event in canonical_trace_events
        ],
        "canonical_trace_path": (
            str(trace_path) if trace_path is not None else None
        ),
    }


def main(arguments: Optional[Sequence[str]] = None) -> int:
    """Run the integrated demonstration and print its JSON summary."""

    argument_parser = argparse.ArgumentParser(
        description=(
            "Run the claim-denial agent using the local production-shaped "
            "knowledge and validation boundaries."
        )
    )
    argument_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Indent the JSON output for easier human reading.",
    )
    argument_parser.add_argument(
        "--trace-directory",
        type=Path,
        help=(
            "Write the completed canonical JSON Lines trace to this local "
            "directory. Existing traces are never overwritten."
        ),
    )
    argument_parser.add_argument(
        "--code-commit",
        default=DEMO_CODE_COMMIT,
        help=(
            "Commit identifier recorded in the reproducible run configuration."
        ),
    )
    parsed_arguments = argument_parser.parse_args(arguments)

    summary = run_integrated_demo(
        trace_output_directory=parsed_arguments.trace_directory,
        code_commit=parsed_arguments.code_commit,
    )
    indentation = 2 if parsed_arguments.pretty else None

    print(
        json.dumps(
            summary,
            indent=indentation,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
