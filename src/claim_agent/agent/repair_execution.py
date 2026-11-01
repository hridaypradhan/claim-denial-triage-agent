"""Perform one budgeted model-output repair for the offline runtime.

Repair execution is separate from the main loop so malformed output and
repairable validation failures follow the same budget, request, and trace rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from claim_agent.agent.model_interface import ModelClient, ModelRequest
from claim_agent.agent.repair_context import build_repair_context
from claim_agent.agent.runtime_budget import (
    RuntimeBudgetLimits,
    RuntimeBudgetUsage,
)
from claim_agent.agent.runtime_state import AgentRuntimeState, RuntimePhase
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace

# TODO(#6): Replace plain validation problem strings with the approved
# ValidationResult problem contract.
# TODO(#9): Integrate this repair operation into malformed-output and
# repairable-validation paths in OfflineAgentRuntime.


@dataclass(frozen=True)
class RepairExecutionOutcome:
    """Values produced by one completed repair model call."""

    runtime_state: AgentRuntimeState
    budget_usage: RuntimeBudgetUsage
    repaired_response_text: str
    repair_task_context: str


def execute_repair_model_call(
    model_client: ModelClient,
    budget_limits: RuntimeBudgetLimits,
    budget_usage: RuntimeBudgetUsage,
    runtime_state: AgentRuntimeState,
    runtime_trace: InMemoryRuntimeTrace,
    system_instructions: str,
    task_context: str,
    rejected_output: str,
    problem_messages: Tuple[str, ...],
    allowed_tool_names: Tuple[str, ...],
) -> RepairExecutionOutcome:
    """Perform one model call dedicated to correcting rejected output.

    Budget capacity is checked before entering the REPAIRING phase. If no repair
    capacity remains, RuntimeBudgetExceededError is raised without calling the
    model.
    """

    updated_budget_usage = budget_usage.record_repair_model_call(
        budget_limits
    )
    repairing_state = runtime_state.transition_to(RuntimePhase.REPAIRING)
    repair_task_context = build_repair_context(
        task_context=task_context,
        rejected_output=rejected_output,
        problem_messages=problem_messages,
    )

    model_call_number = updated_budget_usage.model_calls_used
    request_id = f"{runtime_state.run_id}-model-{model_call_number}"
    repair_request = ModelRequest(
        request_id=request_id,
        system_instructions=system_instructions,
        task_context=repair_task_context,
        allowed_tool_names=allowed_tool_names,
    )

    runtime_trace.record(
        "REPAIR_ATTEMPTED",
        {
            "request_id": request_id,
            "problem_count": len(problem_messages),
        },
    )
    runtime_trace.record(
        "MODEL_REQUESTED",
        {
            "request_id": request_id,
            "is_repair": True,
        },
    )

    repair_response = model_client.generate_response(
        repair_request
    )
    runtime_trace.record(
        "MODEL_RESPONDED",
        {
            "request_id": repair_response.request_id,
            "provider_request_id": (
                repair_response.provider_request_id or ""
            ),
            "is_repair": True,
        },
    )

    return RepairExecutionOutcome(
        runtime_state=repairing_state,
        budget_usage=updated_budget_usage,
        repaired_response_text=repair_response.response_text,
        repair_task_context=repair_task_context,
    )
