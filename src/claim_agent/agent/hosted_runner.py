"""Run the integrated claim workflow through a hosted model client.

The execution function accepts an injected model client so normal tests can
exercise the complete hosted boundary without credentials or network access.
The command-line entry point will construct the real Voyager client separately.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence
from uuid import uuid4

from claim_agent.agent.hosted.configuration import (
    build_hosted_run_configuration,
)
from claim_agent.agent.hosted.factory import (
    PROVISIONAL_VOYAGER_PROFILE,
    HostedModelConfigurationError,
    HostedModelProfile,
    build_hosted_model_client,
)
from claim_agent.agent.integrated_demo_setup import (
    DEMO_AS_OF,
    DEMO_BUDGET_LIMITS,
    DEMO_CODE_COMMIT,
    DEMO_PACKAGE_VERSION,
    KNOWLEDGE_ROOT,
    build_demo_baseline_finding,
    build_demo_claim_context,
    build_demo_dispatcher,
    build_demo_route_decision,
)
from claim_agent.agent.integrated_runtime import run_integrated_case
from claim_agent.agent.model_interface import ModelClient
from claim_agent.agent.planning_prompt import (
    PLANNING_SYSTEM_INSTRUCTIONS,
)
from claim_agent.agent.run_configuration import AllowedTool
from claim_agent.agent.trace_adapter import CanonicalTraceAdapter
from claim_agent.agent.trace_writer import JsonLinesTraceWriter
from claim_agent.tools.knowledge_store import KnowledgeStore

HOSTED_DEMO_RUN_ID = "hosted-demo-medical-necessity"


def create_hosted_run_id() -> str:
    """Create a filesystem-safe identifier for one live hosted run."""

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"hosted-demo-{timestamp}-{uuid4().hex}"


def run_hosted_demo(
    *,
    run_id: str = HOSTED_DEMO_RUN_ID,
    model_client: ModelClient | None = None,
    profile: HostedModelProfile = PROVISIONAL_VOYAGER_PROFILE,
    trace_output_directory: Path | None = None,
    code_commit: str = DEMO_CODE_COMMIT,
) -> Mapping[str, object]:
    """Run one production-shaped case through a hosted model boundary.

    Supplying ``model_client`` is intended for offline tests. When it is not
    supplied, the client factory reads the developer's local API key.
    """

    knowledge_store = KnowledgeStore(KNOWLEDGE_ROOT)
    route_decision = build_demo_route_decision()
    allowed_tools = tuple(
        AllowedTool(
            tool_name=tool_name,
            tool_version="0.1",
        )
        for tool_name in sorted(route_decision.authorized_tools)
    )
    run_configuration = build_hosted_run_configuration(
        code_commit=code_commit,
        knowledge_snapshot_id=knowledge_store.snapshot_id,
        allowed_tools=allowed_tools,
        budget_limits=DEMO_BUDGET_LIMITS,
        profile=profile,
    )
    active_model_client = (
        model_client
        if model_client is not None
        else build_hosted_model_client(profile=profile)
    )

    integrated_result = run_integrated_case(
        run_id=run_id,
        system_instructions=PLANNING_SYSTEM_INSTRUCTIONS,
        model_client=active_model_client,
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
    canonical_events = CanonicalTraceAdapter(
        run_configuration=run_configuration,
        route_decision=route_decision,
    ).adapt(runtime_result.trace_entries)

    trace_path = None
    if trace_output_directory is not None:
        trace_path = JsonLinesTraceWriter(trace_output_directory).write(
            canonical_events
        )

    return {
        "run_id": runtime_result.final_state.run_id,
        "phase": runtime_result.final_state.phase.value,
        "terminal_reason": runtime_result.final_state.terminal_reason,
        "model_provider": run_configuration.model_provider,
        "model_id": run_configuration.model_id,
        "config_sha256": canonical_events[0].payload["config_sha256"],
        "validated_decision": (
            dict(runtime_result.validated_decision)
            if runtime_result.validated_decision is not None
            else None
        ),
        "output_package": integrated_result.output_package.model_dump(
            mode="json"
        ),
        "budget_usage": {
            "model_calls": runtime_result.budget_usage.model_calls_used,
            "tool_calls": runtime_result.budget_usage.tool_calls_used,
            "repair_attempts": (
                runtime_result.budget_usage.repair_attempts_used
            ),
            "total_steps": runtime_result.budget_usage.total_steps_used,
        },
        "retrieved_passage_ids": [
            passage.passage_id for passage in integrated_result.retrieved_evidence
        ],
        "canonical_trace_events": [
            event.event_type.value for event in canonical_events
        ],
        "canonical_trace_path": (
            str(trace_path) if trace_path is not None else None
        ),
    }


def main(arguments: Optional[Sequence[str]] = None) -> int:
    """Run one live hosted case and print its non-secret JSON summary."""

    argument_parser = argparse.ArgumentParser(
        description=(
            "Run the production-shaped claim workflow through the "
            "configured hosted model."
        )
    )
    argument_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Indent the JSON summary for easier reading.",
    )
    argument_parser.add_argument(
        "--trace-directory",
        type=Path,
        default=Path(".local-output") / "hosted-traces",
        help=(
            "Directory for the canonical JSON Lines trace. "
            "Defaults to .local-output/hosted-traces."
        ),
    )
    argument_parser.add_argument(
        "--code-commit",
        default=DEMO_CODE_COMMIT,
        help=(
            "Commit identifier recorded in the reproducible "
            "run configuration."
        ),
    )
    parsed_arguments = argument_parser.parse_args(arguments)

    try:
        summary = run_hosted_demo(
            run_id=create_hosted_run_id(),
            trace_output_directory=parsed_arguments.trace_directory,
            code_commit=parsed_arguments.code_commit,
        )
    except HostedModelConfigurationError as error:
        print(
            json.dumps(
                {
                    "error": "hosted_configuration_error",
                    "message": str(error),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    print(
        json.dumps(
            summary,
            indent=2 if parsed_arguments.pretty else None,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
