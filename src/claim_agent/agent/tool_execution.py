"""Execute one allowlisted tool action for the offline agent runtime.

Keeping tool execution in a focused function makes the central planning loop
easier to read and ensures every tool follows the same budget, state, trace,
and evidence-update rules.
"""

from __future__ import annotations

from dataclasses import dataclass

from claim_agent.agent.action_parser import (
    ParsedModelAction,
    RuntimeActionType,
)
from claim_agent.agent.evidence_context import append_tool_observation
from claim_agent.agent.runtime_budget import (
    RuntimeBudgetLimits,
    RuntimeBudgetUsage,
)
from claim_agent.agent.runtime_ports import (
    OfflineToolObservation,
    ToolDispatcherPort,
)
from claim_agent.agent.runtime_state import AgentRuntimeState, RuntimePhase
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace

# TODO(#3): Replace the temporary dispatcher and observation types with the
# approved ToolResult and EvidencePassage contract adapter.


class ToolNotAllowedError(ValueError):
    """Raised when a model requests a tool outside its allowlist."""


@dataclass(frozen=True)
class ToolExecutionOutcome:
    """Updated runtime values produced by one completed tool action."""

    runtime_state: AgentRuntimeState
    budget_usage: RuntimeBudgetUsage
    task_context: str
    observation: OfflineToolObservation


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
    waiting_state = runtime_state.transition_to(RuntimePhase.WAITING_FOR_TOOL)
    runtime_trace.record(
        "TOOL_REQUESTED",
        {
            "tool_name": action.tool_name,
            "argument_count": len(action.tool_arguments),
        },
    )

    observation = tool_dispatcher.dispatch(
        action.tool_name,
        action.tool_arguments,
    )
    runtime_trace.record(
        "TOOL_RETURNED",
        {
            "tool_name": action.tool_name,
            "succeeded": observation.succeeded,
            "error_code": observation.error_code or "",
            "retryable": observation.retryable,
            "source_count": len(observation.source_ids),
        },
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
