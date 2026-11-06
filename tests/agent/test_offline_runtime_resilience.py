"""Tests for offline runtime budgets and boundary failures."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from claim_agent.agent.model_interface import ModelResponse
from claim_agent.agent.offline_runtime import OfflineAgentRuntime
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from claim_agent.agent.runtime_ports import (
    OfflineToolObservation,
    OfflineValidationResult,
    OfflineValidationStatus,
)
from claim_agent.agent.runtime_state import RuntimePhase
from claim_agent.agent.scripted_model import (
    ScriptedModelClient,
    ScriptedModelOutput,
)
from claim_agent.agent.scripted_ports import (
    ExpectedToolCall,
    ScriptedDecisionValidator,
    ScriptedToolDispatcher,
)
from tests.agent.runtime_test_support import make_test_limits


class OfflineRuntimeResilienceTests(unittest.TestCase):
    """Verify timeouts, exhausted budgets, and safe boundary failures."""

    def test_runtime_timeout_stops_after_slow_model_response(self) -> None:
        """Elapsed runtime capacity should end in explicit manual review."""

        model_client = ScriptedModelClient([ScriptedModelOutput(response_text="""
                    {
                        "action": "propose_decision",
                        "decision": {
                            "recommended_disposition": "APPEAL"
                        }
                    }
                    """)])
        monotonic_clock = Mock(
            side_effect=[
                100.0,
                100.0,
                101.0,
            ]
        )
        runtime = OfflineAgentRuntime(
            model_client=model_client,
            tool_dispatcher=ScriptedToolDispatcher([]),
            decision_validator=ScriptedDecisionValidator([]),
            budget_limits=RuntimeBudgetLimits(
                max_model_calls=1,
                max_tool_calls=1,
                max_repair_attempts=0,
                max_total_steps=2,
                max_runtime_seconds=0.5,
            ),
            allowed_tool_names=("retrieve_medical_policy",),
            monotonic_clock=monotonic_clock,
        )

        result = runtime.run(
            run_id="run-timeout",
            system_instructions="Return a structured decision.",
            task_context="Review one synthetic denial.",
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "runtime_timeout",
        )
        self.assertEqual(result.budget_usage.model_calls_used, 1)
        self.assertEqual(len(model_client.received_requests), 1)

    def test_exhausted_model_budget_stops_before_another_call(
        self,
    ) -> None:
        """A tool observation cannot authorize an extra planning call."""

        model_client = ScriptedModelClient([ScriptedModelOutput(response_text="""
                    {
                        "action": "call_tool",
                        "tool_name": "retrieve_medical_policy",
                        "tool_arguments": {
                            "payer": "BCBS_AZ",
                            "procedure_code": "70553"
                        }
                    }
                    """)])
        tool_dispatcher = ScriptedToolDispatcher(
            [
                ExpectedToolCall(
                    tool_name="retrieve_medical_policy",
                    tool_arguments={
                        "payer": "BCBS_AZ",
                        "procedure_code": "70553",
                    },
                    observation=OfflineToolObservation(
                        succeeded=True,
                        payload={"policy_title": "Synthetic MRI policy"},
                        source_ids=("POLICY-1",),
                    ),
                )
            ]
        )
        runtime = OfflineAgentRuntime(
            model_client=model_client,
            tool_dispatcher=tool_dispatcher,
            decision_validator=ScriptedDecisionValidator([]),
            budget_limits=RuntimeBudgetLimits(
                max_model_calls=1,
                max_tool_calls=1,
                max_repair_attempts=0,
                max_total_steps=2,
            ),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        result = runtime.run(
            run_id="run-model-budget",
            system_instructions="Use retrieved evidence.",
            task_context="Review one synthetic MRI denial.",
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "model_budget_exhausted",
        )
        self.assertEqual(result.budget_usage.model_calls_used, 1)
        self.assertEqual(result.budget_usage.tool_calls_used, 1)
        self.assertEqual(len(model_client.received_requests), 1)

    def test_dispatcher_exception_stops_for_manual_review(self) -> None:
        """An unexpected tool timeout must not escape the runtime."""

        class TimeoutToolDispatcher:
            """Simulate a production dispatcher that fails unexpectedly."""

            def dispatch(
                self,
                request_id: str,
                tool_name: str,
                tool_arguments: object,
            ) -> OfflineToolObservation:
                """Raise instead of returning a normal failed observation."""

                raise TimeoutError("The synthetic policy service did not respond.")

        model_client = ScriptedModelClient([ScriptedModelOutput(response_text="""
                    {
                        "action": "call_tool",
                        "tool_name": "retrieve_medical_policy",
                        "tool_arguments": {
                            "payer": "BCBS_AZ",
                            "procedure_code": "70553"
                        }
                    }
                    """)])
        runtime = OfflineAgentRuntime(
            model_client=model_client,
            tool_dispatcher=TimeoutToolDispatcher(),
            decision_validator=ScriptedDecisionValidator([]),
            budget_limits=make_test_limits(),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        result = runtime.run(
            run_id="run-tool-boundary-error",
            system_instructions="Stop safely when retrieval fails.",
            task_context="Review one synthetic MRI denial.",
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "tool_boundary_error",
        )
        self.assertEqual(result.budget_usage.model_calls_used, 1)
        self.assertEqual(result.budget_usage.tool_calls_used, 1)
        self.assertEqual(
            [entry.event_name for entry in result.trace_entries][-2:],
            ["TOOL_FAILED", "MANUAL_REVIEW"],
        )
        self.assertEqual(len(model_client.received_requests), 1)

    def test_model_exception_stops_for_manual_review(self) -> None:
        """An unexpected model failure must not escape the runtime."""

        class TimeoutModelClient:
            """Simulate a hosted model boundary that fails unexpectedly."""

            def generate_response(self, request: object) -> object:
                """Raise instead of returning a model response."""

                raise TimeoutError("The synthetic model service did not respond.")

        runtime = OfflineAgentRuntime(
            model_client=TimeoutModelClient(),
            tool_dispatcher=ScriptedToolDispatcher([]),
            decision_validator=ScriptedDecisionValidator([]),
            budget_limits=make_test_limits(),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        result = runtime.run(
            run_id="run-model-boundary-error",
            system_instructions="Stop safely when the model fails.",
            task_context="Review one synthetic MRI denial.",
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "model_boundary_error",
        )
        self.assertEqual(result.budget_usage.model_calls_used, 1)
        self.assertEqual(result.budget_usage.tool_calls_used, 0)
        self.assertEqual(
            [entry.event_name for entry in result.trace_entries][-2:],
            ["MODEL_FAILED", "MANUAL_REVIEW"],
        )

    def test_validator_exception_stops_for_manual_review(self) -> None:
        """An unexpected validator failure must not approve the decision."""

        class FailingDecisionValidator:
            """Simulate deterministic validation becoming unavailable."""

            def validate(self, decision_payload: object) -> object:
                """Raise instead of returning a validation result."""

                raise RuntimeError(
                    "The synthetic validator could not evaluate the decision."
                )

        model_client = ScriptedModelClient([ScriptedModelOutput(response_text="""
                    {
                        "action": "propose_decision",
                        "decision": {
                            "recommended_disposition": "APPEAL",
                            "rationale": "Synthetic policy supports appeal."
                        }
                    }
                    """)])
        runtime = OfflineAgentRuntime(
            model_client=model_client,
            tool_dispatcher=ScriptedToolDispatcher([]),
            decision_validator=FailingDecisionValidator(),
            budget_limits=make_test_limits(),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        result = runtime.run(
            run_id="run-validation-boundary-error",
            system_instructions="Never bypass deterministic validation.",
            task_context="Review one synthetic MRI denial.",
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "validation_boundary_error",
        )
        self.assertEqual(result.budget_usage.model_calls_used, 1)
        self.assertEqual(
            [entry.event_name for entry in result.trace_entries][-2:],
            ["VALIDATION_FAILED", "MANUAL_REVIEW"],
        )
        self.assertIsNone(result.validated_decision)

    def test_repair_model_exception_preserves_consumed_budgets(
        self,
    ) -> None:
        """A failed repair call must remain visible and budgeted."""

        class RepairTimeoutModelClient:
            """Return malformed output, then fail during its correction."""

            def __init__(self) -> None:
                """Track how many model requests have been received."""

                self.received_request_count = 0

            def generate_response(self, request: object) -> object:
                """Return one malformed response before timing out."""

                self.received_request_count += 1

                if self.received_request_count == 1:
                    return ModelResponse(
                        request_id=request.request_id,
                        response_text='{"action": "propose_decision"',
                        provider_request_id="initial-malformed-response",
                    )

                raise TimeoutError("The synthetic repair model did not respond.")

        model_client = RepairTimeoutModelClient()
        runtime = OfflineAgentRuntime(
            model_client=model_client,
            tool_dispatcher=ScriptedToolDispatcher([]),
            decision_validator=ScriptedDecisionValidator([]),
            budget_limits=make_test_limits(),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        result = runtime.run(
            run_id="run-repair-boundary-error",
            system_instructions="Repair malformed structured output once.",
            task_context="Review one synthetic MRI denial.",
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "model_boundary_error",
        )
        self.assertEqual(result.budget_usage.model_calls_used, 2)
        self.assertEqual(result.budget_usage.repair_attempts_used, 1)
        self.assertEqual(result.budget_usage.total_steps_used, 2)
        self.assertEqual(model_client.received_request_count, 2)
        self.assertEqual(
            [entry.event_name for entry in result.trace_entries][-2:],
            ["MODEL_FAILED", "MANUAL_REVIEW"],
        )


if __name__ == "__main__":
    unittest.main()
