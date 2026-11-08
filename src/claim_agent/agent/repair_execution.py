"""Perform one budgeted model-output repair for the offline runtime.

Repair execution is separate from the main loop so malformed output and
repairable validation failures follow the same budget, request, and trace rules.
"""

from __future__ import annotations

import hashlib
import time
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
from claim_agent.contracts import ValidationProblem


class RepairModelBoundaryError(RuntimeError):
    """Carry consumed runtime values when a repair model call fails."""

    def __init__(
        self,
        runtime_state: AgentRuntimeState,
        budget_usage: RuntimeBudgetUsage,
        original_error: Exception,
    ) -> None:
        """Preserve the repair phase and all consumed budget capacity."""

        self.runtime_state = runtime_state
        self.budget_usage = budget_usage
        self.original_error = original_error

        super().__init__(
            "The repair model boundary raised " f"{type(original_error).__name__}."
        )


@dataclass(frozen=True)
class RepairExecutionOutcome:
    """Values produced by one completed repair model call."""

    runtime_state: AgentRuntimeState
    budget_usage: RuntimeBudgetUsage
    repaired_response_text: str
    repair_task_context: str
    request_id: str


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
    validation_problems: Tuple[ValidationProblem, ...] = (),
    repair_kind: str = "OUTPUT_PARSE",
    rejected_request_id: str | None = None,
) -> RepairExecutionOutcome:
    """Perform one model call dedicated to correcting rejected output.

    Budget capacity is checked before entering the REPAIRING phase. If no repair
    capacity remains, RuntimeBudgetExceededError is raised without calling the
    model.
    """

    updated_budget_usage = budget_usage.record_repair_model_call(budget_limits)
    repairing_state = runtime_state.transition_to(RuntimePhase.REPAIRING)
    repair_task_context = build_repair_context(
        task_context=task_context,
        rejected_output=rejected_output,
        problem_messages=problem_messages,
        validation_problems=validation_problems,
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
            "repair_index": updated_budget_usage.repair_attempts_used,
            "repair_kind": repair_kind,
            "rejected_request_id": rejected_request_id or request_id,
        },
    )
    runtime_trace.record(
        "MODEL_REQUESTED",
        {
            "request_id": request_id,
            "is_repair": True,
            "prompt_sha256": hashlib.sha256(
                (
                    system_instructions
                    + "\n\n"
                    + repair_task_context
                ).encode("utf-8")
            ).hexdigest(),
            "repair_attempt": updated_budget_usage.repair_attempts_used,
        },
    )

    model_request_started_at = time.monotonic()
    try:
        repair_response = model_client.generate_response(repair_request)
    except Exception as error:
        # The repair attempt has already consumed its model, repair, and
        # total-step budgets. Preserve those values when the boundary fails.
        runtime_trace.record(
            "MODEL_FAILED",
            {
                "request_id": request_id,
                "error_type": type(error).__name__,
                "is_repair": True,
                "failure_class": (
                    "REQUEST_TIMEOUT"
                    if isinstance(error, TimeoutError)
                    else "BOUNDARY_EXCEPTION"
                ),
                "retryable": False,
                "repair_attempt": updated_budget_usage.repair_attempts_used,
                "elapsed_ms": max(
                    0,
                    round(
                        (
                            time.monotonic()
                            - model_request_started_at
                        )
                        * 1000
                    ),
                ),
            },
        )
        raise RepairModelBoundaryError(
            runtime_state=repairing_state,
            budget_usage=updated_budget_usage,
            original_error=error,
        ) from error

    runtime_trace.record(
        "MODEL_RESPONDED",
        {
            "request_id": repair_response.request_id,
            "provider_request_id": (repair_response.provider_request_id or ""),
            "is_repair": True,
            "response_text": repair_response.response_text,
            "input_tokens": repair_response.input_token_count,
            "output_tokens": repair_response.output_token_count,
            "repair_attempt": updated_budget_usage.repair_attempts_used,
            "elapsed_ms": max(
                0,
                round(
                    (
                        time.monotonic()
                        - model_request_started_at
                    )
                    * 1000
                ),
            ),
        },
    )

    return RepairExecutionOutcome(
        runtime_state=repairing_state,
        budget_usage=updated_budget_usage,
        repaired_response_text=repair_response.response_text,
        repair_task_context=repair_task_context,
        request_id=request_id,
    )
