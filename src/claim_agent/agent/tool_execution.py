"""Execute one allowlisted tool action for the offline agent runtime.

Keeping tool execution in a focused function makes the central planning loop
easier to read and ensures every tool follows the same budget, state, trace,
and evidence-update rules.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from claim_agent.agent.action_parser import (
    ParsedModelAction,
    RuntimeActionType,
)
from claim_agent.agent.evidence_context import append_tool_observation
from claim_agent.agent.runtime_budget import (
    RuntimeBudgetExceededError,
    RuntimeBudgetLimits,
    RuntimeBudgetUsage,
)
from claim_agent.agent.runtime_ports import (
    OfflineToolObservation,
    ToolDispatcherPort,
)
from claim_agent.agent.runtime_state import AgentRuntimeState, RuntimePhase
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace

# This control loop uses the stable scripted compatibility type. The integrated
# production boundary adapts shared ToolResult records before they enter here.


class ToolNotAllowedError(ValueError):
    """Raised when a model requests a tool outside its allowlist."""


class ToolBoundaryError(RuntimeError):
    """Carry runtime values when the dispatcher raises unexpectedly."""

    def __init__(
        self,
        runtime_state: AgentRuntimeState,
        budget_usage: RuntimeBudgetUsage,
        original_error: Exception,
    ) -> None:
        """Preserve consumed capacity and describe the failed boundary."""

        self.runtime_state = runtime_state
        self.budget_usage = budget_usage
        self.original_error = original_error

        super().__init__(
            "The tool dispatcher raised "
            f"{type(original_error).__name__}."
        )


class ToolRetryBudgetExceededError(RuntimeError):
    """Preserve the first tool attempt when its retry cannot start."""

    def __init__(
        self,
        runtime_state: AgentRuntimeState,
        budget_usage: RuntimeBudgetUsage,
        original_error: RuntimeBudgetExceededError,
    ) -> None:
        """Carry the consumed first attempt to the runtime terminal path."""

        self.runtime_state = runtime_state
        self.budget_usage = budget_usage
        self.original_error = original_error

        super().__init__(str(original_error))


@dataclass(frozen=True)
class ToolExecutionOutcome:
    """Updated runtime values produced by one completed tool action."""

    runtime_state: AgentRuntimeState
    budget_usage: RuntimeBudgetUsage
    task_context: str
    observation: OfflineToolObservation


def _dispatch_tool_request(
    request_id: str,
    tool_name: str,
    tool_arguments: dict[str, object],
    tool_dispatcher: ToolDispatcherPort,
    waiting_state: AgentRuntimeState,
    budget_usage: RuntimeBudgetUsage,
    runtime_trace: InMemoryRuntimeTrace,
    attempt: int,
) -> tuple[OfflineToolObservation, int]:
    """Dispatch one tool attempt and preserve state if its boundary raises."""

    request_started_at = time.monotonic()
    try:
        observation = tool_dispatcher.dispatch(
            request_id,
            tool_name,
            tool_arguments,
        )
        elapsed_ms = max(
            0,
            round((time.monotonic() - request_started_at) * 1000),
        )
        return observation, elapsed_ms
    except Exception as error:
        # A raised exception is different from a normal ToolResult failure.
        # Preserve the budget already consumed by this exact attempt.
        runtime_trace.record(
            "TOOL_FAILED",
            {
                "request_id": request_id,
                "tool_name": tool_name,
                "error_type": type(error).__name__,
                "attempt": attempt,
                "failure_class": "BOUNDARY_EXCEPTION",
                "retryable": False,
                "elapsed_ms": max(
                    0,
                    round(
                        (time.monotonic() - request_started_at) * 1000
                    ),
                ),
            },
        )
        raise ToolBoundaryError(
            runtime_state=waiting_state,
            budget_usage=budget_usage,
            original_error=error,
        ) from error


def _record_tool_result(
    request_id: str,
    tool_name: str,
    observation: OfflineToolObservation,
    runtime_trace: InMemoryRuntimeTrace,
    attempt: int,
    elapsed_ms: int,
) -> None:
    """Record one returned observation using its matching request identity."""

    evidence_passages = observation.payload.get(
        "evidence_passages",
        (),
    )
    passage_ids = [
        passage["passage_id"]
        for passage in evidence_passages
        if isinstance(passage, dict) and "passage_id" in passage
    ]

    runtime_trace.record(
        "TOOL_RETURNED",
        {
            "request_id": request_id,
            "tool_name": tool_name,
            "succeeded": observation.succeeded,
            "error_code": observation.error_code or "",
            "retryable": observation.retryable,
            "status": "OK" if observation.succeeded else "ERROR",
            "attempt": attempt,
            "source_count": len(observation.source_ids),
            "source_ids": list(observation.source_ids),
            "passage_ids": passage_ids,
            "elapsed_ms": elapsed_ms,
        },
    )


def execute_tool_action(
    action: ParsedModelAction,
    allowed_tool_names: tuple[str, ...],
    tool_dispatcher: ToolDispatcherPort,
    budget_limits: RuntimeBudgetLimits,
    budget_usage: RuntimeBudgetUsage,
    runtime_state: AgentRuntimeState,
    runtime_trace: InMemoryRuntimeTrace,
    task_context: str,
) -> ToolExecutionOutcome:
    """Execute one validated, allowlisted tool request.

    The caller remains responsible for converting raised budget or allowlist
    errors into a terminal manual-review result.
    """

    if action.action_type is not RuntimeActionType.CALL_TOOL:
        raise ValueError("execute_tool_action requires a CALL_TOOL action")

    if action.tool_name is None:
        raise ValueError("A CALL_TOOL action requires tool_name")

    if action.tool_name not in allowed_tool_names:
        raise ToolNotAllowedError(
            f"Tool {action.tool_name!r} is not in the runtime allowlist."
        )

    updated_budget_usage = budget_usage.record_tool_call(budget_limits)
    request_id = (
        f"{runtime_state.run_id}:tool:{updated_budget_usage.tool_calls_used}"
    )
    waiting_state = runtime_state.transition_to(RuntimePhase.WAITING_FOR_TOOL)
    runtime_trace.record(
        "TOOL_REQUESTED",
        {
            "request_id": request_id,
            "tool_name": action.tool_name,
            "argument_count": len(action.tool_arguments),
            "tool_arguments": dict(action.tool_arguments),
            "tool_args": dict(action.tool_arguments),
            "tool_call_index": updated_budget_usage.tool_calls_used,
            "attempt": 1,
        },
    )

    observation, elapsed_ms = _dispatch_tool_request(
        request_id=request_id,
        tool_name=action.tool_name,
        tool_arguments=dict(action.tool_arguments),
        tool_dispatcher=tool_dispatcher,
        waiting_state=waiting_state,
        budget_usage=updated_budget_usage,
        runtime_trace=runtime_trace,
        attempt=1,
    )
    _record_tool_result(
        request_id=request_id,
        tool_name=action.tool_name,
        observation=observation,
        runtime_trace=runtime_trace,
        attempt=1,
        elapsed_ms=elapsed_ms,
    )

    if observation.retryable:
        previous_request_id = request_id
        try:
            updated_budget_usage = updated_budget_usage.record_tool_call(
                budget_limits
            )
        except RuntimeBudgetExceededError as error:
            raise ToolRetryBudgetExceededError(
                runtime_state=waiting_state,
                budget_usage=updated_budget_usage,
                original_error=error,
            ) from error
        request_id = (
            f"{runtime_state.run_id}:tool:"
            f"{updated_budget_usage.tool_calls_used}"
        )
        runtime_trace.record(
            "TOOL_RETRY_REQUESTED",
            {
                "request_id": request_id,
                "previous_request_id": previous_request_id,
                "tool_name": action.tool_name,
                "error_code": observation.error_code or "",
                "argument_count": len(action.tool_arguments),
                "tool_arguments": dict(action.tool_arguments),
                "tool_args": dict(action.tool_arguments),
                "tool_call_index": (
                    updated_budget_usage.tool_calls_used - 1
                ),
                "attempt": 2,
            },
        )

        observation, elapsed_ms = _dispatch_tool_request(
            request_id=request_id,
            tool_name=action.tool_name,
            tool_arguments=dict(action.tool_arguments),
            tool_dispatcher=tool_dispatcher,
            waiting_state=waiting_state,
            budget_usage=updated_budget_usage,
            runtime_trace=runtime_trace,
            attempt=2,
        )
        _record_tool_result(
            request_id=request_id,
            tool_name=action.tool_name,
            observation=observation,
            runtime_trace=runtime_trace,
            attempt=2,
            elapsed_ms=elapsed_ms,
        )

    updating_state = waiting_state.transition_to(RuntimePhase.UPDATING_EVIDENCE)
    updated_context = append_tool_observation(
        task_context=task_context,
        tool_name=action.tool_name,
        observation=observation,
    )
    planning_state = updating_state.transition_to(RuntimePhase.PLANNING)
    runtime_trace.record(
        "PHASE_CHANGED",
        {"phase": planning_state.phase.value},
    )

    return ToolExecutionOutcome(
        runtime_state=planning_state,
        budget_usage=updated_budget_usage,
        task_context=updated_context,
        observation=observation,
    )
