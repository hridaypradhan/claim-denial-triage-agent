"""Execute one development case through a selected hosted model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from claim_agent.agent.canonical_trace import TraceEvent
from claim_agent.agent.hosted.configuration import (
    build_hosted_run_configuration,
)
from claim_agent.agent.hosted.factory import HostedModelProfile
from claim_agent.agent.integrated_runtime import run_integrated_case
from claim_agent.agent.model_interface import ModelClient
from claim_agent.agent.planning_prompt import (
    PLANNING_SYSTEM_INSTRUCTIONS,
)
from claim_agent.agent.run_configuration import AllowedTool
from claim_agent.agent.trace_adapter import CanonicalTraceAdapter
from claim_agent.contracts import OutputPackage
from claim_agent.tools.dispatcher import ToolDispatcher
from claim_agent.tools.encounter_notes import claim_document_inventory
from claim_agent.tools.knowledge_store import KnowledgeStore
from evaluation.hosted_comparison import HostedComparisonBudgets
from evaluation.scripted_agent import (
    EvaluationCasePreparationError,
    PreparedEvaluationCase,
)


@dataclass(frozen=True)
class HostedCaseExecution:
    """Auditable artifacts produced by one hosted case run."""

    output_package: OutputPackage
    canonical_trace_events: tuple[TraceEvent, ...]


def build_hosted_tool_context(
    *,
    prepared_case: PreparedEvaluationCase,
    knowledge_store: KnowledgeStore,
) -> dict[str, object]:
    """Describe authorized tools using only claim-bound manifest data."""

    claim_context = prepared_case.claim_context
    if claim_context is None:
        raise EvaluationCasePreparationError(
            f"Case {prepared_case.case.case_id!r} has no trusted "
            "claim context."
        )

    encounter_documents = claim_document_inventory(
        knowledge_store,
        claim_context.claim_id,
    )

    return {
        "retrieve_medical_policy": {
            "purpose": "Retrieve the applicable policy and its criteria.",
            "payer": claim_context.payer,
            "procedure_code": claim_context.procedure_code,
            "service_date": claim_context.service_date.isoformat(),
            "query_guidance": (
                "Use a short clinical query relevant to the denial."
            ),
        },
        "get_encounter_note": {
            "purpose": (
                "Retrieve one encounter document belonging to the active claim."
            ),
            "claim_id": claim_context.claim_id,
            "available_document_ids": [
                document.document_id for document in encounter_documents
            ],
        },
    }


def execute_hosted_case(
    *,
    prepared_case: PreparedEvaluationCase,
    model_client: ModelClient,
    profile: HostedModelProfile,
    budgets: HostedComparisonBudgets,
    knowledge_store: KnowledgeStore,
    run_id: str,
    as_of: date,
    code_commit: str,
    plan_version: str,
    repeat_index: int,
    package_version: str = "v1",
) -> HostedCaseExecution:
    """Run one agent-authorized case with frozen comparison settings."""

    if not prepared_case.requires_agent:
        raise EvaluationCasePreparationError(
            f"Case {prepared_case.case.case_id!r} does not require "
            "hosted agent execution."
        )

    if prepared_case.claim_context is None:
        raise EvaluationCasePreparationError(
            f"Case {prepared_case.case.case_id!r} has no trusted "
            "claim context."
        )

    allowed_tools = tuple(
        AllowedTool(
            tool_name=tool_name,
            tool_version="0.1",
        )
        for tool_name in sorted(prepared_case.route_decision.authorized_tools)
    )
    run_configuration = build_hosted_run_configuration(
        code_commit=code_commit,
        knowledge_snapshot_id=knowledge_store.snapshot_id,
        allowed_tools=allowed_tools,
        budget_limits=budgets.runtime_limits(),
        profile=profile,
        max_tool_retries=budgets.max_tool_retries,
        tool_request_timeout_seconds=budgets.tool_request_timeout_seconds,
    )

    hosted_tool_context = build_hosted_tool_context(
        prepared_case=prepared_case,
        knowledge_store=knowledge_store,
    )
    encounter_tool_context = hosted_tool_context["get_encounter_note"]
    available_document_ids = encounter_tool_context[
        "available_document_ids"
    ]
    if not isinstance(available_document_ids, list):
        raise EvaluationCasePreparationError(
            "available_document_ids must be a list"
        )

    integrated_result = run_integrated_case(
        run_id=run_id,
        system_instructions=PLANNING_SYSTEM_INSTRUCTIONS,
        model_client=model_client,
        dispatcher=ToolDispatcher(knowledge_store),
        baseline_finding=prepared_case.baseline_finding,
        claim_context=prepared_case.claim_context,
        route_decision=prepared_case.route_decision,
        budget_limits=budgets.runtime_limits(),
        as_of=as_of,
        package_version=package_version,
        knowledge_snapshot_id=knowledge_store.snapshot_id,
        tool_context=hosted_tool_context,
        required_evidence_source_ids=tuple(
            str(document_id) for document_id in available_document_ids
        ),
    )

    canonical_trace_events = CanonicalTraceAdapter(
        run_configuration=run_configuration,
        route_decision=prepared_case.route_decision,
        evaluation_context={
            "experiment_id": plan_version,
            "repeat_index": repeat_index,
            "scenario_family_id": prepared_case.case.scenario_family_id,
            "system": "hosted-comparison",
        },
    ).adapt(integrated_result.runtime_result.trace_entries)

    return HostedCaseExecution(
        output_package=integrated_result.output_package,
        canonical_trace_events=canonical_trace_events,
    )
