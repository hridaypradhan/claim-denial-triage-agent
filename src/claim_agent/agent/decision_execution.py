"""Validate and repair proposed decisions for the offline runtime.

Keeping decision handling separate leaves the main runtime focused on planning,
tool coordination, budgets, and terminal lifecycle control.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional, Tuple

from claim_agent.agent.action_parser import (
    ModelOutputParseError,
    ParsedModelAction,
    RuntimeActionType,
    parse_model_action,
)
from claim_agent.agent.model_interface import ModelClient
from claim_agent.agent.repair_execution import (
    RepairModelBoundaryError,
    execute_repair_model_call,
)
from claim_agent.agent.runtime_budget import (
    RuntimeBudgetExceededError,
    RuntimeBudgetLimits,
    RuntimeBudgetUsage,
)
from claim_agent.agent.runtime_ports import (
    DecisionValidatorPort,
    OfflineValidationStatus,
)
from claim_agent.agent.runtime_state import AgentRuntimeState, RuntimePhase
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace

if TYPE_CHECKING:
    from claim_agent.agent.offline_runtime import OfflineRuntimeResult


def execute_decision_with_repair(
    model_client: ModelClient,
    decision_validator: DecisionValidatorPort,
    budget_limits: RuntimeBudgetLimits,
    allowed_tool_names: Tuple[str, ...],
    timeout_result_if_expired: Callable[..., Optional["OfflineRuntimeResult"]],
    finish_manual_review: Callable[..., "OfflineRuntimeResult"],
    result_factory: Callable[..., "OfflineRuntimeResult"],
    parsed_action: ParsedModelAction,
    current_response_text: str,
    current_request_id: str,
    system_instructions: str,
    current_task_context: str,
    runtime_started_at: float,
    runtime_state: AgentRuntimeState,
    budget_usage: RuntimeBudgetUsage,
    runtime_trace: InMemoryRuntimeTrace,
) -> "OfflineRuntimeResult":
    """Validate a proposed decision with at most one correction attempt.

    The shared repair budget enforces the one-attempt limit. A corrected
    decision always returns through deterministic validation before the run
    can complete.
    """

    while True:
        timeout_result = timeout_result_if_expired(
            runtime_started_at=runtime_started_at,
            runtime_state=runtime_state,
            budget_usage=budget_usage,
            runtime_trace=runtime_trace,
        )
        if timeout_result is not None:
            return timeout_result

        runtime_state = runtime_state.transition_to(RuntimePhase.PROPOSING_DECISION)
        runtime_trace.record(
            "DECISION_PROPOSED",
            {
                "decision_field_count": len(parsed_action.decision_payload),
                "decision": dict(parsed_action.decision_payload),
            },
        )

        runtime_state = runtime_state.transition_to(RuntimePhase.VALIDATING)
        try:
            validation_result = decision_validator.validate(
                parsed_action.decision_payload
            )
        except Exception as error:
            # Deterministic validation is a runtime boundary. An
            # unexpected implementation failure must not approve or lose
            # the proposed claim decision.
            runtime_trace.record(
                "VALIDATION_FAILED",
                {
                    "error_type": type(error).__name__,
                    "decision_id": parsed_action.decision_payload.get(
                        "decision_id",
                        "UNKNOWN_DECISION",
                    ),
                    "failure_class": "BOUNDARY_EXCEPTION",
                    "retryable": False,
                },
            )
            return finish_manual_review(
                runtime_state=runtime_state,
                budget_usage=budget_usage,
                runtime_trace=runtime_trace,
                terminal_reason="validation_boundary_error",
                problem_message=(
                    "The validation boundary raised " f"{type(error).__name__}."
                ),
            )

        runtime_trace.record(
            "VALIDATION_RETURNED",
            {
                "status": validation_result.status.value,
                "decision_id": parsed_action.decision_payload.get(
                    "decision_id",
                    "UNKNOWN_DECISION",
                ),
                "claim_id": parsed_action.decision_payload.get(
                    "claim_id",
                    "UNKNOWN_CLAIM",
                ),
                "service_line_id": parsed_action.decision_payload.get(
                    "service_line_id",
                    0,
                ),
                "validation_problems": [
                    problem.model_dump(mode="json")
                    for problem in validation_result.validation_problems
                ],
                "validated_decision": (
                    None
                    if validation_result.validated_decision is None
                    else dict(validation_result.validated_decision)
                ),
                "manual_review_reasons": list(
                    validation_result.manual_review_reasons
                ),
            },
        )

        timeout_result = timeout_result_if_expired(
            runtime_started_at=runtime_started_at,
            runtime_state=runtime_state,
            budget_usage=budget_usage,
            runtime_trace=runtime_trace,
        )
        if timeout_result is not None:
            return timeout_result

        if validation_result.status is OfflineValidationStatus.ACCEPTED:
            completed_state = runtime_state.transition_to(
                RuntimePhase.COMPLETED,
                terminal_reason="decision_accepted",
            )
            runtime_trace.record(
                "RUN_COMPLETED",
                {"terminal_reason": "decision_accepted"},
            )

            return result_factory(
                final_state=completed_state,
                budget_usage=budget_usage,
                trace_entries=runtime_trace.entries,
                validated_decision=(validation_result.validated_decision),
            )

        problem_messages = validation_result.problem_messages or (
            "Deterministic validation did not accept " "the decision.",
        )
        problem_message = "; ".join(problem_messages)

        if validation_result.status is OfflineValidationStatus.MANUAL_REVIEW:
            return finish_manual_review(
                runtime_state=runtime_state,
                budget_usage=budget_usage,
                runtime_trace=runtime_trace,
                terminal_reason="decision_requires_manual_review",
                problem_message=problem_message,
            )

        try:
            repair_outcome = execute_repair_model_call(
                model_client=model_client,
                budget_limits=budget_limits,
                budget_usage=budget_usage,
                runtime_state=runtime_state,
                runtime_trace=runtime_trace,
                system_instructions=system_instructions,
                task_context=current_task_context,
                rejected_output=current_response_text,
                problem_messages=problem_messages,
                validation_problems=(
                    validation_result.validation_problems
                ),
                allowed_tool_names=allowed_tool_names,
                repair_kind="VALIDATION",
                rejected_request_id=current_request_id,
            )
        except RepairModelBoundaryError as error:
            return finish_manual_review(
                runtime_state=error.runtime_state,
                budget_usage=error.budget_usage,
                runtime_trace=runtime_trace,
                terminal_reason="model_boundary_error",
                problem_message=str(error),
            )
        except RuntimeBudgetExceededError as budget_error:
            return finish_manual_review(
                runtime_state=runtime_state,
                budget_usage=budget_usage,
                runtime_trace=runtime_trace,
                terminal_reason="repair_budget_exhausted",
                problem_message=str(budget_error),
                budget_name=budget_error.budget_name,
            )

        runtime_state = repair_outcome.runtime_state
        budget_usage = repair_outcome.budget_usage
        current_response_text = repair_outcome.repaired_response_text
        current_request_id = repair_outcome.request_id

        timeout_result = timeout_result_if_expired(
            runtime_started_at=runtime_started_at,
            runtime_state=runtime_state,
            budget_usage=budget_usage,
            runtime_trace=runtime_trace,
        )
        if timeout_result is not None:
            return timeout_result

        try:
            parsed_action = parse_model_action(current_response_text)
        except ModelOutputParseError as repair_parse_error:
            return finish_manual_review(
                runtime_state=runtime_state,
                budget_usage=budget_usage,
                runtime_trace=runtime_trace,
                terminal_reason="repair_output_invalid",
                problem_message=str(repair_parse_error),
            )

        if parsed_action.action_type is RuntimeActionType.REQUEST_MANUAL_REVIEW:
            return finish_manual_review(
                runtime_state=runtime_state,
                budget_usage=budget_usage,
                runtime_trace=runtime_trace,
                terminal_reason="repair_requested_manual_review",
                problem_message=(
                    parsed_action.manual_review_reason
                    or "The repair requested manual review."
                ),
            )

        if parsed_action.action_type is not RuntimeActionType.PROPOSE_DECISION:
            return finish_manual_review(
                runtime_state=runtime_state,
                budget_usage=budget_usage,
                runtime_trace=runtime_trace,
                terminal_reason="invalid_validation_repair_action",
                problem_message=(
                    "A validation repair must return a proposed decision."
                ),
            )

        # The loop validates the corrected decision. A second REPAIRABLE
        # result cannot cause another model call because the shared repair
        # budget permits only one attempt.

