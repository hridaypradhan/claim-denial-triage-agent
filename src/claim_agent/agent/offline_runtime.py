"""Offline coordinator for one bounded agent run.

This first runtime-loop slice supports a proposed decision, deterministic
validation, successful completion, and conservative manual review. Tool
execution and bounded repair will be added in the next implementation pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

from claim_agent.agent.action_parser import (
    ModelOutputParseError,
    RuntimeActionType,
    parse_model_action,
)
from claim_agent.agent.model_interface import ModelClient, ModelRequest
from claim_agent.agent.runtime_budget import (
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

# TODO(#5): Replace the free-form task_context input with an adapter around the
# approved BaselineFinding and RouteDecision contracts.
# TODO(#9): Add allowlisted tool dispatch, evidence-state updates, and bounded
# model-output repair in the second runtime-loop pass.


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

        This first pass performs one planning call. A later pass will repeat
        planning after tool observations and will repair malformed output.
        """

        runtime_state = AgentRuntimeState(run_id=run_id)
        budget_usage = RuntimeBudgetUsage()
        runtime_trace = InMemoryRuntimeTrace(run_id=run_id)

        runtime_trace.record(
            "RUN_STARTED",
            {"phase": runtime_state.phase.value},
        )

        runtime_state = runtime_state.transition_to(RuntimePhase.PLANNING)
        runtime_trace.record(
            "PHASE_CHANGED",
            {"phase": runtime_state.phase.value},
        )

        budget_usage = budget_usage.record_model_call(self._budget_limits)
        request_id = f"{run_id}-model-1"
        model_request = ModelRequest(
            request_id=request_id,
            system_instructions=system_instructions,
            task_context=task_context,
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

        try:
            parsed_action = parse_model_action(model_response.response_text)
        except ModelOutputParseError as error:
            return self._finish_manual_review(
                runtime_state=runtime_state,
                budget_usage=budget_usage,
                runtime_trace=runtime_trace,
                terminal_reason="model_output_invalid",
                problem_message=str(error),
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
            return self._finish_manual_review(
                runtime_state=runtime_state,
                budget_usage=budget_usage,
                runtime_trace=runtime_trace,
                terminal_reason="tool_execution_not_enabled",
                problem_message=(
                    "Tool actions are deferred to the second runtime pass."
                ),
            )

        runtime_state = runtime_state.transition_to(
            RuntimePhase.PROPOSING_DECISION
        )
        runtime_trace.record(
            "DECISION_PROPOSED",
            {"decision_field_count": len(parsed_action.decision_payload)},
        )

        runtime_state = runtime_state.transition_to(RuntimePhase.VALIDATING)
        validation_result = self._decision_validator.validate(
            parsed_action.decision_payload
        )
        runtime_trace.record(
            "VALIDATION_RETURNED",
            {"status": validation_result.status.value},
        )

        if validation_result.status is OfflineValidationStatus.ACCEPTED:
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
                validated_decision=(validation_result.validated_decision),
            )

        problem_message = (
            "; ".join(validation_result.problem_messages)
            or "Deterministic validation did not accept the decision."
        )
        terminal_reason = (
            "decision_repair_deferred"
            if validation_result.status is OfflineValidationStatus.REPAIRABLE
            else "decision_requires_manual_review"
        )

        return self._finish_manual_review(
            runtime_state=runtime_state,
            budget_usage=budget_usage,
            runtime_trace=runtime_trace,
            terminal_reason=terminal_reason,
            problem_message=problem_message,
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
