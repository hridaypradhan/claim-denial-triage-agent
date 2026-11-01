"""Offline coordinator for one bounded agent run.

The runtime supports repeated allowlisted tool use, deterministic validation,
one bounded repair attempt, successful completion, and conservative manual
review.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

from claim_agent.agent.action_parser import (
    ModelOutputParseError,
    ParsedModelAction,
    RuntimeActionType,
    parse_model_action,
)
from claim_agent.agent.model_interface import ModelClient, ModelRequest
from claim_agent.agent.repair_execution import (
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
    ToolDispatcherPort,
)
from claim_agent.agent.runtime_state import AgentRuntimeState, RuntimePhase
from claim_agent.agent.runtime_trace import (
    InMemoryRuntimeTrace,
    RuntimeTraceEntry,
)
from claim_agent.agent.tool_execution import (
    ToolNotAllowedError,
    execute_tool_action,
)

# TODO(#5): Replace the free-form task_context input with an adapter around the
# approved BaselineFinding and RouteDecision contracts.


@dataclass(frozen=True)
class OfflineRuntimeResult:
    """Final observable result of one offline agent run.

    Attributes:
        final_state: Terminal lifecycle state reached by the runtime.
        budget_usage: Model, tool, repair, and total-step capacity consumed.
        trace_entries: Ordered audit history for the run.
        validated_decision: Decision accepted by deterministic validation.
            Manual-review results do not contain an accepted decision.
    """

    final_state: AgentRuntimeState
    budget_usage: RuntimeBudgetUsage
    trace_entries: Tuple[RuntimeTraceEntry, ...]
    validated_decision: Optional[Mapping[str, object]] = None

    def __post_init__(self) -> None:
        """Protect an accepted decision from later modification."""

        if not self.final_state.is_terminal:
            raise ValueError(
                "OfflineRuntimeResult requires a terminal final_state"
            )

        if self.validated_decision is not None:
            protected_decision = MappingProxyType(
                dict(self.validated_decision)
            )
            object.__setattr__(
                self,
                "validated_decision",
                protected_decision,
            )


class OfflineAgentRuntime:
    """Coordinate the model, parser, validator, budgets, state, and trace."""

    def __init__(
        self,
        model_client: ModelClient,
        tool_dispatcher: ToolDispatcherPort,
        decision_validator: DecisionValidatorPort,
        budget_limits: RuntimeBudgetLimits,
        allowed_tool_names: Tuple[str, ...],
    ) -> None:
        """Store replaceable boundaries, limits, and the tool allowlist."""

        if any(not tool_name.strip() for tool_name in allowed_tool_names):
            raise ValueError(
                "allowed_tool_names cannot contain blank values"
            )

        if len(set(allowed_tool_names)) != len(allowed_tool_names):
            raise ValueError(
                "allowed_tool_names cannot contain duplicates"
            )

        self._model_client = model_client
        self._tool_dispatcher = tool_dispatcher
        self._decision_validator = decision_validator
        self._budget_limits = budget_limits
        self._allowed_tool_names = allowed_tool_names

    def run(
        self,
        run_id: str,
        system_instructions: str,
        task_context: str,
    ) -> OfflineRuntimeResult:
        """Run one offline case until completion or manual review.

        Each planning turn may request one allowlisted tool, propose a decision,
        or stop for manual review. A successful tool result is appended to the
        task context before the next planning turn.
        """

        runtime_state = AgentRuntimeState(run_id=run_id)
        budget_usage = RuntimeBudgetUsage()
        runtime_trace = InMemoryRuntimeTrace(run_id=run_id)
        current_task_context = task_context

        runtime_trace.record(
            "RUN_STARTED",
            {"phase": runtime_state.phase.value},
        )

        runtime_state = runtime_state.transition_to(RuntimePhase.PLANNING)
        runtime_trace.record(
            "PHASE_CHANGED",
            {"phase": runtime_state.phase.value},
        )

        while True:
            try:
                budget_usage = budget_usage.record_model_call(
                    self._budget_limits
                )
            except RuntimeBudgetExceededError as error:
                return self._finish_manual_review(
                    runtime_state=runtime_state,
                    budget_usage=budget_usage,
                    runtime_trace=runtime_trace,
                    terminal_reason="model_budget_exhausted",
                    problem_message=str(error),
                )

            model_call_number = budget_usage.model_calls_used
            request_id = f"{run_id}-model-{model_call_number}"
            model_request = ModelRequest(
                request_id=request_id,
                system_instructions=system_instructions,
                task_context=current_task_context,
                allowed_tool_names=self._allowed_tool_names,
            )

            runtime_trace.record(
                "MODEL_REQUESTED",
                {"request_id": request_id},
            )
            model_response = self._model_client.generate_response(model_request)
            runtime_trace.record(
                "MODEL_RESPONDED",
                {
                    "request_id": model_response.request_id,
                    "provider_request_id": (
                        model_response.provider_request_id or ""
                    ),
                },
            )
            # Keep the exact response so validation repair can show the model
            # which rejected decision it must correct.
            current_response_text = model_response.response_text
            try:
                parsed_action = parse_model_action(
                    model_response.response_text
                )
            except ModelOutputParseError as initial_parse_error:
                try:
                    repair_outcome = execute_repair_model_call(
                        model_client=self._model_client,
                        budget_limits=self._budget_limits,
                        budget_usage=budget_usage,
                        runtime_state=runtime_state,
                        runtime_trace=runtime_trace,
                        system_instructions=system_instructions,
                        task_context=current_task_context,
                        rejected_output=model_response.response_text,
                        problem_messages=(
                            str(initial_parse_error),
                        ),
                        allowed_tool_names=self._allowed_tool_names,
                    )
                except RuntimeBudgetExceededError as budget_error:
                    return self._finish_manual_review(
                        runtime_state=runtime_state,
                        budget_usage=budget_usage,
                        runtime_trace=runtime_trace,
                        terminal_reason="repair_budget_exhausted",
                        problem_message=str(budget_error),
                    )

                runtime_state = repair_outcome.runtime_state
                budget_usage = repair_outcome.budget_usage
                current_response_text = (
                    repair_outcome.repaired_response_text
                )
                try:
                    parsed_action = parse_model_action(
                        repair_outcome.repaired_response_text
                    )
                except ModelOutputParseError as repair_parse_error:
                    return self._finish_manual_review(
                        runtime_state=runtime_state,
                        budget_usage=budget_usage,
                        runtime_trace=runtime_trace,
                        terminal_reason="repair_output_invalid",
                        problem_message=str(repair_parse_error),
                    )

                # Tool execution begins from PLANNING. Decision and
                # manual-review actions can transition directly from REPAIRING.
                if (
                    parsed_action.action_type
                    is RuntimeActionType.CALL_TOOL
                ):
                    runtime_state = runtime_state.transition_to(
                        RuntimePhase.PLANNING
                    )

            if parsed_action.action_type is RuntimeActionType.REQUEST_MANUAL_REVIEW:
                return self._finish_manual_review(
                    runtime_state=runtime_state,
                    budget_usage=budget_usage,
                    runtime_trace=runtime_trace,
                    terminal_reason="model_requested_manual_review",
                    problem_message=(
                        parsed_action.manual_review_reason
                        or "The model requested manual review."
                    ),
                )

            if parsed_action.action_type is RuntimeActionType.CALL_TOOL:
                try:
                    tool_outcome = execute_tool_action(
                        action=parsed_action,
                        allowed_tool_names=self._allowed_tool_names,
                        tool_dispatcher=self._tool_dispatcher,
                        budget_limits=self._budget_limits,
                        budget_usage=budget_usage,
                        runtime_state=runtime_state,
                        runtime_trace=runtime_trace,
                        task_context=current_task_context,
                    )
                except ToolNotAllowedError as error:
                    return self._finish_manual_review(
                        runtime_state=runtime_state,
                        budget_usage=budget_usage,
                        runtime_trace=runtime_trace,
                        terminal_reason="tool_not_allowed",
                        problem_message=str(error),
                    )
                except RuntimeBudgetExceededError as error:
                    return self._finish_manual_review(
                        runtime_state=runtime_state,
                        budget_usage=budget_usage,
                        runtime_trace=runtime_trace,
                        terminal_reason="tool_budget_exhausted",
                        problem_message=str(error),
                    )

                runtime_state = tool_outcome.runtime_state
                budget_usage = tool_outcome.budget_usage
                current_task_context = tool_outcome.task_context

                # The next loop iteration gives the model the new observation.
                continue

            return self._validate_decision_with_repair(
                parsed_action=parsed_action,
                current_response_text=current_response_text,
                system_instructions=system_instructions,
                current_task_context=current_task_context,
                runtime_state=runtime_state,
                budget_usage=budget_usage,
                runtime_trace=runtime_trace,
            )

    def _validate_decision_with_repair(
        self,
        parsed_action: ParsedModelAction,
        current_response_text: str,
        system_instructions: str,
        current_task_context: str,
        runtime_state: AgentRuntimeState,
        budget_usage: RuntimeBudgetUsage,
        runtime_trace: InMemoryRuntimeTrace,
    ) -> OfflineRuntimeResult:
        """Validate a proposed decision with at most one correction attempt.

        The shared repair budget enforces the one-attempt limit. A corrected
        decision always returns through deterministic validation before the run
        can complete.
        """

        while True:
            runtime_state = runtime_state.transition_to(
                RuntimePhase.PROPOSING_DECISION
            )
            runtime_trace.record(
                "DECISION_PROPOSED",
                {
                    "decision_field_count": len(
                        parsed_action.decision_payload
                    )
                },
            )

            runtime_state = runtime_state.transition_to(
                RuntimePhase.VALIDATING
            )
            validation_result = self._decision_validator.validate(
                parsed_action.decision_payload
            )
            runtime_trace.record(
                "VALIDATION_RETURNED",
                {"status": validation_result.status.value},
            )

            if (
                validation_result.status
                is OfflineValidationStatus.ACCEPTED
            ):
                completed_state = runtime_state.transition_to(
                    RuntimePhase.COMPLETED,
                    terminal_reason="decision_accepted",
                )
                runtime_trace.record(
                    "RUN_COMPLETED",
                    {"terminal_reason": "decision_accepted"},
                )

                return OfflineRuntimeResult(
                    final_state=completed_state,
                    budget_usage=budget_usage,
                    trace_entries=runtime_trace.entries,
                    validated_decision=(
                        validation_result.validated_decision
                    ),
                )

            problem_messages = (
                validation_result.problem_messages
                or (
                    "Deterministic validation did not accept "
                    "the decision.",
                )
            )
            problem_message = "; ".join(problem_messages)

            if (
                validation_result.status
                is OfflineValidationStatus.MANUAL_REVIEW
            ):
                return self._finish_manual_review(
                    runtime_state=runtime_state,
                    budget_usage=budget_usage,
                    runtime_trace=runtime_trace,
                    terminal_reason="decision_requires_manual_review",
                    problem_message=problem_message,
                )

            try:
                repair_outcome = execute_repair_model_call(
                    model_client=self._model_client,
                    budget_limits=self._budget_limits,
                    budget_usage=budget_usage,
                    runtime_state=runtime_state,
                    runtime_trace=runtime_trace,
                    system_instructions=system_instructions,
                    task_context=current_task_context,
                    rejected_output=current_response_text,
                    problem_messages=problem_messages,
                    allowed_tool_names=self._allowed_tool_names,
                )
            except RuntimeBudgetExceededError as budget_error:
                return self._finish_manual_review(
                    runtime_state=runtime_state,
                    budget_usage=budget_usage,
                    runtime_trace=runtime_trace,
                    terminal_reason="repair_budget_exhausted",
                    problem_message=str(budget_error),
                )

            runtime_state = repair_outcome.runtime_state
            budget_usage = repair_outcome.budget_usage
            current_response_text = repair_outcome.repaired_response_text

            try:
                parsed_action = parse_model_action(current_response_text)
            except ModelOutputParseError as repair_parse_error:
                return self._finish_manual_review(
                    runtime_state=runtime_state,
                    budget_usage=budget_usage,
                    runtime_trace=runtime_trace,
                    terminal_reason="repair_output_invalid",
                    problem_message=str(repair_parse_error),
                )

            if (
                parsed_action.action_type
                is RuntimeActionType.REQUEST_MANUAL_REVIEW
            ):
                return self._finish_manual_review(
                    runtime_state=runtime_state,
                    budget_usage=budget_usage,
                    runtime_trace=runtime_trace,
                    terminal_reason="repair_requested_manual_review",
                    problem_message=(
                        parsed_action.manual_review_reason
                        or "The repair requested manual review."
                    ),
                )

            if (
                parsed_action.action_type
                is not RuntimeActionType.PROPOSE_DECISION
            ):
                return self._finish_manual_review(
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

    @staticmethod
    def _finish_manual_review(
        runtime_state: AgentRuntimeState,
        budget_usage: RuntimeBudgetUsage,
        runtime_trace: InMemoryRuntimeTrace,
        terminal_reason: str,
        problem_message: str,
    ) -> OfflineRuntimeResult:
        """Create a terminal manual-review result and its trace entry."""

        manual_review_state = runtime_state.transition_to(
            RuntimePhase.MANUAL_REVIEW,
            terminal_reason=terminal_reason,
        )
        runtime_trace.record(
            "MANUAL_REVIEW",
            {
                "terminal_reason": terminal_reason,
                "problem_message": problem_message,
            },
        )

        return OfflineRuntimeResult(
            final_state=manual_review_state,
            budget_usage=budget_usage,
            trace_entries=runtime_trace.entries,
        )
