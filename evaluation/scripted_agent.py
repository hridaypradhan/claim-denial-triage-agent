"""Reusable scripted-model plans for evaluation cases.

Each evaluation run needs a fresh model client because scripted responses are
consumed as the runtime uses them. Keeping the immutable plan separate from the
stateful client makes repeated runs deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

from baseline.triage.claim import load_claim

from claim_agent.agent.canonical_trace import TraceEvent
from claim_agent.agent.integrated_runtime import run_integrated_case
from claim_agent.agent.router import analyze_and_route_claim
from claim_agent.agent.run_configuration import (
    AgentRunConfig,
    AllowedTool,
    ModelParameters,
)
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from claim_agent.agent.scripted_model import (
    ScriptedModelClient,
    ScriptedModelOutput,
)
from claim_agent.agent.tool_request_guard import (
    ClaimToolContext,
    build_claim_tool_context,
)
from claim_agent.agent.trace_adapter import CanonicalTraceAdapter
from claim_agent.contracts import (
    BaselineFinding,
    OutputPackage,
    RouteDecision,
    RouteOutcome,
)
from claim_agent.output.package import create_output_package
from claim_agent.tools.dispatcher import ToolDispatcher
from claim_agent.tools.knowledge_store import KnowledgeStore
from evaluation.pilot_data import Case

# Most cases need one policy and one encounter lookup. Cases with evidence
# spread across two encounter documents need one additional bounded tool call.
SCRIPTED_EVALUATION_BUDGETS = RuntimeBudgetLimits(
    max_model_calls=4,
    max_tool_calls=3,
    max_repair_attempts=1,
    max_total_steps=7,
    max_runtime_seconds=30.0,
)


class ScriptedCasePlanError(ValueError):
    """Raised when an evaluation case has no usable scripted plan."""


class EvaluationCasePreparationError(ValueError):
    """Raised when a dataset case cannot be routed unambiguously."""


@dataclass(frozen=True)
class ScriptedCasePlan:
    """Instructions and ordered model responses for one evaluation case."""

    system_instructions: str
    model_outputs: tuple[ScriptedModelOutput, ...]

    def __post_init__(self) -> None:
        """Reject plans that could not drive a runtime model call."""

        if not self.system_instructions.strip():
            raise ScriptedCasePlanError(
                "system_instructions must contain a non-whitespace value"
            )

        if not self.model_outputs:
            raise ScriptedCasePlanError(
                "model_outputs must contain at least one scripted response"
            )

    def create_model_client(self) -> ScriptedModelClient:
        """Create a new client with an unconsumed response queue."""

        return ScriptedModelClient(self.model_outputs)


def scripted_plan_for_case(
    case_id: str,
    plans_by_case_id: Mapping[str, ScriptedCasePlan],
) -> ScriptedCasePlan:
    """Return the configured plan or identify the unsupported case clearly."""

    try:
        return plans_by_case_id[case_id]
    except KeyError as error:
        raise ScriptedCasePlanError(
            f"No scripted agent plan is configured for case {case_id!r}."
        ) from error


@dataclass(frozen=True)
class PreparedEvaluationCase:
    """Trusted routing inputs prepared from one evaluation case."""

    case: Case
    baseline_finding: BaselineFinding
    route_decision: RouteDecision
    claim_context: ClaimToolContext | None

    @property
    def requires_agent(self) -> bool:
        """Report whether this case must enter the bounded agent runtime."""

        return self.route_decision.outcome is RouteOutcome.AGENT_REQUIRED


@dataclass(frozen=True)
class ScriptedAgentExecution:
    """Evaluation artifacts produced by one bounded agent run."""

    output_package: OutputPackage
    canonical_trace_events: tuple[TraceEvent, ...]


def prepare_evaluation_case(
    case: Case,
    as_of: date,
) -> PreparedEvaluationCase:
    """Load, route, and validate one evaluation case.

    The dataset identifies a single service line, while the claim file may
    contain multiple lines. Selecting exactly one matching routed result keeps
    one line's evidence and decision from leaking into another line's run.
    """

    claim = load_claim(case.claim_path)

    if claim.claim_id != case.claim_id:
        raise EvaluationCasePreparationError(
            f"Case {case.case_id!r} expects claim {case.claim_id!r}, "
            f"but the file contains {claim.claim_id!r}."
        )

    matching_routes = [
        (baseline_finding, route_decision)
        for baseline_finding, route_decision in analyze_and_route_claim(
            claim,
            as_of,
        )
        if baseline_finding.service_line_id == case.service_line_id
    ]

    if len(matching_routes) != 1:
        raise EvaluationCasePreparationError(
            f"Case {case.case_id!r} expected one routed service line, "
            f"but found {len(matching_routes)}."
        )

    baseline_finding, route_decision = matching_routes[0]

    # Only agent-routed cases need trusted fields for claim-bound retrieval.
    # Deterministic and manual-review cases never cross a tool boundary.
    claim_context = (
        build_claim_tool_context(
            claim=claim,
            service_line_id=case.service_line_id,
        )
        if route_decision.outcome is RouteOutcome.AGENT_REQUIRED
        else None
    )

    return PreparedEvaluationCase(
        case=case,
        baseline_finding=baseline_finding,
        route_decision=route_decision,
        claim_context=claim_context,
    )


def build_non_agent_output_package(
    prepared_case: PreparedEvaluationCase,
    run_id: str,
    package_version: str,
    knowledge_snapshot_id: str,
) -> OutputPackage:
    """Package a deterministic or manual-review route without invoking a model.

    These routes have already reached their safe result through the frozen
    baseline and router. Calling the agent would add unnecessary behavior and
    could change a result that should remain deterministic.
    """

    if prepared_case.requires_agent:
        raise EvaluationCasePreparationError(
            f"Case {prepared_case.case.case_id!r} requires agent execution "
            "and cannot use the non-agent package path."
        )

    return create_output_package(
        run_id=run_id,
        package_version=package_version,
        knowledge_snapshot_id=knowledge_snapshot_id,
        finding=prepared_case.baseline_finding,
        route_outcome=prepared_case.route_decision.outcome,
    )


def build_scripted_run_configuration(
    prepared_case: PreparedEvaluationCase,
    knowledge_snapshot_id: str,
    code_commit: str,
) -> AgentRunConfig:
    """Freeze the settings needed to reproduce one scripted evaluation run."""

    allowed_tools = tuple(
        AllowedTool(
            tool_name=tool_name,
            tool_version="0.1",
        )
        for tool_name in sorted(
            prepared_case.route_decision.authorized_tools
        )
    )

    return AgentRunConfig(
        config_version="0.2",
        code_commit=code_commit,
        model_provider="scripted",
        model_id="scripted-model-v1",
        model_parameters=ModelParameters(
            max_output_tokens=4096,
            temperature=0.0,
            top_p=None,
        ),
        prompt_version="milestone-2-pilot-v1",
        runtime_seed=0,
        provider_seed=None,
        provider_seed_supported=False,
        max_model_calls=SCRIPTED_EVALUATION_BUDGETS.max_model_calls,
        max_tool_calls=SCRIPTED_EVALUATION_BUDGETS.max_tool_calls,
        max_repair_attempts=(
            SCRIPTED_EVALUATION_BUDGETS.max_repair_attempts
        ),
        max_total_steps=SCRIPTED_EVALUATION_BUDGETS.max_total_steps,
        max_tool_retries=1,
        max_runtime_seconds=(
            SCRIPTED_EVALUATION_BUDGETS.max_runtime_seconds
        ),
        model_request_timeout_seconds=30.0,
        tool_request_timeout_seconds=30.0,
        allowed_tools=allowed_tools,
        knowledge_snapshot_id=knowledge_snapshot_id,
    )


def execute_scripted_agent_case(
    prepared_case: PreparedEvaluationCase,
    plan: ScriptedCasePlan,
    knowledge_store: KnowledgeStore,
    run_id: str,
    as_of: date,
    code_commit: str = "working-tree",
    package_version: str = "v1",
) -> ScriptedAgentExecution:
    """Execute one agent-authorized case and expose its evaluation artifacts."""

    if not prepared_case.requires_agent:
        raise EvaluationCasePreparationError(
            f"Case {prepared_case.case.case_id!r} does not require "
            "agent execution."
        )

    if prepared_case.claim_context is None:
        raise EvaluationCasePreparationError(
            f"Case {prepared_case.case.case_id!r} has no trusted "
            "claim context."
        )

    run_configuration = build_scripted_run_configuration(
        prepared_case=prepared_case,
        knowledge_snapshot_id=knowledge_store.snapshot_id,
        code_commit=code_commit,
    )

    integrated_result = run_integrated_case(
        run_id=run_id,
        system_instructions=plan.system_instructions,
        model_client=plan.create_model_client(),
        dispatcher=ToolDispatcher(knowledge_store),
        baseline_finding=prepared_case.baseline_finding,
        claim_context=prepared_case.claim_context,
        route_decision=prepared_case.route_decision,
        budget_limits=SCRIPTED_EVALUATION_BUDGETS,
        as_of=as_of,
        package_version=package_version,
        knowledge_snapshot_id=knowledge_store.snapshot_id,
    )

    canonical_trace_events = CanonicalTraceAdapter(
        run_configuration=run_configuration,
        route_decision=prepared_case.route_decision,
        evaluation_context={
            "system": "scripted-agent",
            "scenario_family_id": (
                prepared_case.case.scenario_family_id
            ),
        },
    ).adapt(integrated_result.runtime_result.trace_entries)

    return ScriptedAgentExecution(
        output_package=integrated_result.output_package,
        canonical_trace_events=canonical_trace_events,
    )
