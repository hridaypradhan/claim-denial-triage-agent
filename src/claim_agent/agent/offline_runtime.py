"""Offline coordinator for one bounded agent run.

The runtime supports repeated allowlisted tool use, deterministic validation,
one bounded repair attempt, successful completion, and conservative manual
review.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping, Optional, Tuple

from claim_agent.agent.action_parser import (
    ModelOutputParseError,
    RuntimeActionType,
    parse_model_action,
)
from claim_agent.agent.decision_execution import execute_decision_with_repair
from claim_agent.agent.model_interface import ModelClient, ModelRequest
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
    ToolDispatcherPort,
)
from claim_agent.agent.runtime_state import AgentRuntimeState, RuntimePhase
from claim_agent.agent.runtime_trace import (
    InMemoryRuntimeTrace,
    RuntimeTraceEntry,
)
from claim_agent.agent.tool_execution import (
    ToolBoundaryError,
    ToolNotAllowedError,
    ToolRetryBudgetExceededError,
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
            raise ValueError("OfflineRuntimeResult requires a terminal final_state")

        if self.validated_decision is not None:
            protected_decision = MappingProxyType(dict(self.validated_decision))
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
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Store boundaries, limits, allowlist, and an injectable clock."""

        if any(not tool_name.strip() for tool_name in allowed_tool_names):
            raise ValueError("allowed_tool_names cannot contain blank values")

        if len(set(allowed_tool_names)) != len(allowed_tool_names):
            raise ValueError("allowed_tool_names cannot contain duplicates")

        self._model_client = model_client
        self._tool_dispatcher = tool_dispatcher
        self._decision_validator = decision_validator
        self._budget_limits = budget_limits
        self._allowed_tool_names = allowed_tool_names
        self._monotonic_clock = monotonic_clock

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
        runtime_started_at = self._monotonic_clock()

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
            timeout_result = self._timeout_result_if_expired(
                runtime_started_at=runtime_started_at,
                runtime_state=runtime_state,
                budget_usage=budget_usage,
                runtime_trace=runtime_trace,
            )
            if timeout_result is not None:
                return timeout_result

            try:
                budget_usage = budget_usage.record_model_call(self._budget_limits)
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
            try:
                model_response = self._model_client.generate_response(model_request)
            except Exception as error:
                # Boundary failures must become traceable manual-review
                # outcomes instead of escaping the claim workflow.
                runtime_trace.record(
                    "MODEL_FAILED",
                    {
                        "request_id": request_id,
                        "error_type": type(error).__name__,
                    },
                )
                return self._finish_manual_review(
                    runtime_state=runtime_state,
                    budget_usage=budget_usage,
                    runtime_trace=runtime_trace,
                    terminal_reason="model_boundary_error",
                    problem_message=(
                        "The model boundary raised " f"{type(error).__name__}."
                    ),
                )

            runtime_trace.record(
                "MODEL_RESPONDED",
                {
                    "request_id": model_response.request_id,
                    "provider_request_id": (model_response.provider_request_id or ""),
                },
            )

            timeout_result = self._timeout_result_if_expired(
                runtime_started_at=runtime_started_at,
                runtime_state=runtime_state,
                budget_usage=budget_usage,
                runtime_trace=runtime_trace,
            )
            if timeout_result is not None:
                return timeout_result

            # Keep the exact response so validation repair can show the model
            # which rejected decision it must correct.
            current_response_text = model_response.response_text
            try:
                parsed_action = parse_model_action(model_response.response_text)
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
                        problem_messages=(str(initial_parse_error),),
                        allowed_tool_names=self._allowed_tool_names,
                    )
                except RepairModelBoundaryError as error:
                    return self._finish_manual_review(
                        runtime_state=error.runtime_state,
                        budget_usage=error.budget_usage,
                        runtime_trace=runtime_trace,
                        terminal_reason="model_boundary_error",
                        problem_message=str(error),
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

                timeout_result = self._timeout_result_if_expired(
                    runtime_started_at=runtime_started_at,
                    runtime_state=runtime_state,
                    budget_usage=budget_usage,
                    runtime_trace=runtime_trace,
                )
                if timeout_result is not None:
                    return timeout_result

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
                if parsed_action.action_type is RuntimeActionType.CALL_TOOL:
                    runtime_state = runtime_state.transition_to(RuntimePhase.PLANNING)

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
                timeout_result = self._timeout_result_if_expired(
                    runtime_started_at=runtime_started_at,
                    runtime_state=runtime_state,
                    budget_usage=budget_usage,
                    runtime_trace=runtime_trace,
                )
                if timeout_result is not None:
                    return timeout_result

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
                except ToolBoundaryError as error:
                    return self._finish_manual_review(
                        runtime_state=error.runtime_state,
                        budget_usage=error.budget_usage,
                        runtime_trace=runtime_trace,
                        terminal_reason="tool_boundary_error",
                        problem_message=str(error),
                    )
                except ToolRetryBudgetExceededError as error:
                    return self._finish_manual_review(
                        runtime_state=error.runtime_state,
                        budget_usage=error.budget_usage,
                        runtime_trace=runtime_trace,
                        terminal_reason="tool_budget_exhausted",
                        problem_message=str(error.original_error),
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

                timeout_result = self._timeout_result_if_expired(
                    runtime_started_at=runtime_started_at,
                    runtime_state=runtime_state,
                    budget_usage=budget_usage,
                    runtime_trace=runtime_trace,
                )
                if timeout_result is not None:
                    return timeout_result

                # The next loop iteration gives the model the new observation.
                continue

            return execute_decision_with_repair(
                model_client=self._model_client,
                decision_validator=self._decision_validator,
                budget_limits=self._budget_limits,
                allowed_tool_names=self._allowed_tool_names,
                timeout_result_if_expired=self._timeout_result_if_expired,
                finish_manual_review=self._finish_manual_review,
                result_factory=OfflineRuntimeResult,
                parsed_action=parsed_action,
                current_response_text=current_response_text,
                system_instructions=system_instructions,
                current_task_context=current_task_context,
                runtime_started_at=runtime_started_at,
                runtime_state=runtime_state,
                budget_usage=budget_usage,
                runtime_trace=runtime_trace,
            )

    def _timeout_result_if_expired(
        self,
        runtime_started_at: float,
        runtime_state: AgentRuntimeState,
        budget_usage: RuntimeBudgetUsage,
        runtime_trace: InMemoryRuntimeTrace,
    ) -> Optional[OfflineRuntimeResult]:
        """Return manual review when the elapsed-time budget is exhausted.

        The injected monotonic clock makes this boundary deterministic in
        tests. Synchronous model and tool calls cannot be interrupted midway;
        the runtime detects an overrun immediately after either call returns.
        Production adapters remain responsible for their own request timeout.
        """

        elapsed_seconds = self._monotonic_clock() - runtime_started_at
        if elapsed_seconds < self._budget_limits.max_runtime_seconds:
            return None

        return self._finish_manual_review(
            runtime_state=runtime_state,
            budget_usage=budget_usage,
            runtime_trace=runtime_trace,
            terminal_reason="runtime_timeout",
            problem_message=(
                "Cannot continue because the runtime timeout budget "
                f"of {self._budget_limits.max_runtime_seconds:g} seconds "
                "was exhausted."
            ),
        )

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
