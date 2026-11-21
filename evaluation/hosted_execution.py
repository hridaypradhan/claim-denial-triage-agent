"""Execute one development case through a selected hosted model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from claim_agent.agent.canonical_trace import TraceEvent
from claim_agent.agent.drafting.interface import DraftingClient
from claim_agent.agent.drafting.post_decision import (
    DRAFTING_ATTEMPTED,
    VERIFICATION_NOT_AGREED,
    PostDecisionDraftingResult,
    run_post_decision_drafting,
)
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
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace, RuntimeTraceEntry
from claim_agent.agent.verification.interface import VerifierClient
from claim_agent.agent.trace_adapter import CanonicalTraceAdapter
from claim_agent.contracts import (
    Disposition,
    DraftingOutcome,
    EvidenceBundle,
    OutputPackage,
    ValidationOutcome,
)
from claim_agent.output.package import create_output_package
from claim_agent.tools.dispatcher import (
    DENIAL_CODE_TOOL,
    ENCOUNTER_NOTE_TOOL,
    MEDICAL_POLICY_TOOL,
    PAYER_PROFILE_TOOL,
    ToolDispatcher,
)
from claim_agent.tools.deadline_tool import DEADLINE_TOOL
from claim_agent.tools.eligibility import (
    ELIGIBILITY_ROOT,
    ELIGIBILITY_TOOL,
)
from claim_agent.tools.encounter_notes import claim_document_inventory
from claim_agent.tools.evidence_bundle import claim_available_sources
from claim_agent.tools.knowledge_store import KnowledgeStore
from evaluation.hosted_comparison import HostedComparisonBudgets
from evaluation.scripted_agent import (
    EvaluationCasePreparationError,
    PreparedEvaluationCase,
)


@dataclass(frozen=True)
class PostDecisionClients:
    """Builds the tool-less verifier and drafter only when a gate needs them."""

    verifier_factory: Callable[[], VerifierClient]
    drafting_factory: Callable[[], DraftingClient]


@dataclass(frozen=True)
class PostDecisionExecution:
    """What the verifier and drafter did after deterministic validation."""

    # The package deterministic validation produced, before these gates.
    decision_package: OutputPackage
    evidence_bundle: EvidenceBundle
    # None when deterministic validation did not accept a decision, so
    # neither gate ran.
    result: Optional[PostDecisionDraftingResult]
    trace_entries: tuple[RuntimeTraceEntry, ...]
    # Why the final package was escalated by these gates, or None.
    escalation_reason: Optional[str]


@dataclass(frozen=True)
class HostedCaseExecution:
    """Auditable artifacts produced by one hosted case run."""

    output_package: OutputPackage
    canonical_trace_events: tuple[TraceEvent, ...]
    # Present only when verification and drafting were requested.
    post_decision: Optional[PostDecisionExecution] = None


def _escalated_package(package: OutputPackage, reason: str) -> OutputPackage:
    """The final package when a post-decision gate fails closed.

    The output contract ties an accepted validation result to its own
    disposition, so the escalated package omits the decision and names the
    gate's reason; the accepted decision is kept in ``decision_package``.
    """

    return create_output_package(
        run_id=package.run_id,
        package_version=package.package_version,
        knowledge_snapshot_id=package.knowledge_snapshot_id,
        finding=package.baseline_finding,
        route_outcome=package.route_outcome,
        proposed_decision=None,
        validation_result=None,
        agent_terminal_reason=reason,
    )


def apply_post_decision_gates(
    *,
    decision_package: OutputPackage,
    evidence_bundle: EvidenceBundle,
    clients: PostDecisionClients,
    run_id: str,
) -> tuple[OutputPackage, PostDecisionExecution]:
    """Run the verifier, then the drafter for appeals, and settle the final package.

    - Deterministic validation did not accept a decision: neither model is
      called and the package is unchanged.
    - The verifier does not agree, fails, or times out: the final package is
      MANUAL_REVIEW with the verifier's reason code.
    - An appeal whose draft is not accepted: MANUAL_REVIEW with the drafting
      reason code. Other dispositions need no draft and keep their decision.
    """

    trace = InMemoryRuntimeTrace(f"{run_id}-post-decision")
    validation = decision_package.validation_result
    decision = decision_package.proposed_decision
    if (
        decision is None
        or validation is None
        or validation.outcome != ValidationOutcome.ACCEPTED
        or validation.validated_decision is None
    ):
        return decision_package, PostDecisionExecution(
            decision_package, evidence_bundle, None, trace.entries, None
        )

    # Build both clients before either gate runs. A configuration error, such
    # as a missing key, then fails the run visibly instead of being recorded
    # as a verifier or drafter provider failure.
    verifier_client = clients.verifier_factory()
    drafting_client = clients.drafting_factory()
    result = run_post_decision_drafting(
        verifier_client=verifier_client,
        drafting_client=drafting_client,
        verification_request_id=f"{run_id}-verify",
        drafting_request_id=f"{run_id}-draft",
        proposed_decision=decision,
        evidence_bundle=evidence_bundle,
        deterministic_validation=validation,
        runtime_trace=trace,
    )

    reason: Optional[str] = None
    if result.stop_reason == VERIFICATION_NOT_AGREED:
        reason = result.verification_execution.validation_result.reason_code
    elif (
        result.stop_reason == DRAFTING_ATTEMPTED
        and validation.validated_decision.recommended_disposition
        == Disposition.APPEAL_WITH_DOCUMENTATION
        and result.drafting_execution.validation_result.outcome
        != DraftingOutcome.ACCEPTED
    ):
        reason = result.drafting_execution.validation_result.reason_code

    final = decision_package if reason is None else _escalated_package(decision_package, reason)
    return final, PostDecisionExecution(
        decision_package, evidence_bundle, result, trace.entries, reason
    )


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

    authorized_tools = set(prepared_case.route_decision.authorized_tools)
    tool_context: dict[str, object] = {}

    if MEDICAL_POLICY_TOOL in authorized_tools:
        tool_context[MEDICAL_POLICY_TOOL] = {
            "purpose": "Retrieve the applicable policy and its criteria.",
            "payer": claim_context.payer,
            "procedure_code": claim_context.procedure_code,
            "service_date": claim_context.service_date.isoformat(),
            "query_guidance": (
                "Use a short clinical query relevant to the denial."
            ),
        }

    if ENCOUNTER_NOTE_TOOL in authorized_tools:
        encounter_documents = claim_document_inventory(
            knowledge_store,
            claim_context.claim_id,
        )
        tool_context[ENCOUNTER_NOTE_TOOL] = {
            "purpose": (
                "Retrieve one encounter document belonging to the "
                "active claim."
            ),
            "claim_id": claim_context.claim_id,
            "available_document_ids": [
                document.document_id for document in encounter_documents
            ],
        }

    if DENIAL_CODE_TOOL in authorized_tools:
        if (
            claim_context.denial_carc is None
            or claim_context.denial_group_code is None
        ):
            raise EvaluationCasePreparationError(
                "Authorized denial-code lookup lacks trusted denial fields."
            )

        tool_context[DENIAL_CODE_TOOL] = {
            "purpose": (
                "Look up the active denial code without guessing "
                "unknown values."
            ),
            "carc": claim_context.denial_carc,
            "rarc": list(claim_context.denial_rarc),
            "group_code": claim_context.denial_group_code,
        }

    if PAYER_PROFILE_TOOL in authorized_tools:
        tool_context[PAYER_PROFILE_TOOL] = {
            "purpose": (
                "Look up the active payer's explicit profile without "
                "using a default."
            ),
            "payer_id": claim_context.payer,
        }

    if DEADLINE_TOOL in authorized_tools:
        tool_context[DEADLINE_TOOL] = {
            "purpose": (
                "Report the frozen baseline finding's deadline without "
                "recomputing payer rules."
            ),
            "claim_id": prepared_case.baseline_finding.claim_id,
            "service_line_id": (prepared_case.baseline_finding.service_line_id),
        }

    if ELIGIBILITY_TOOL in authorized_tools:
        if claim_context.patient_account is None:
            raise EvaluationCasePreparationError(
                "Authorized eligibility retrieval lacks a trusted "
                "patient_account."
            )

        if not claim_context.eligibility_payers:
            raise EvaluationCasePreparationError(
                "Authorized eligibility retrieval has no approved payers."
            )

        tool_context[ELIGIBILITY_TOOL] = {
            "purpose": (
                "Retrieve eligibility evidence for the active member "
                "and an explicitly approved payer."
            ),
            "patient_account": claim_context.patient_account,
            "approved_payers": list(claim_context.eligibility_payers),
        }

    return tool_context


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
    post_decision_clients: Optional[PostDecisionClients] = None,
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
    encounter_tool_context = hosted_tool_context.get(ENCOUNTER_NOTE_TOOL)
    if encounter_tool_context is None:
        available_document_ids: list[str] = []
    else:
        if not isinstance(encounter_tool_context, dict):
            raise EvaluationCasePreparationError(
                "Encounter-note tool context must be an object."
            )

        available_document_ids = encounter_tool_context.get(
            "available_document_ids"
        )
        if not isinstance(available_document_ids, list):
            raise EvaluationCasePreparationError(
                "available_document_ids must be a list"
            )

    eligibility_store = (
        KnowledgeStore(ELIGIBILITY_ROOT)
        if ELIGIBILITY_TOOL in prepared_case.route_decision.authorized_tools
        else None
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
        eligibility_store=eligibility_store,
        tool_context=hosted_tool_context,
        required_evidence_source_ids=tuple(
            str(document_id) for document_id in available_document_ids
        ),
        bundle_available_sources=(
            None
            if post_decision_clients is None
            else claim_available_sources(
                knowledge_store, prepared_case.claim_context.claim_id
            )
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

    if post_decision_clients is None:
        return HostedCaseExecution(
            output_package=integrated_result.output_package,
            canonical_trace_events=canonical_trace_events,
        )

    final_package, post_decision = apply_post_decision_gates(
        decision_package=integrated_result.output_package,
        evidence_bundle=integrated_result.evidence_bundle,
        clients=post_decision_clients,
        run_id=run_id,
    )
    return HostedCaseExecution(
        output_package=final_package,
        canonical_trace_events=canonical_trace_events,
        post_decision=post_decision,
    )
