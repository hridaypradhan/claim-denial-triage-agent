"""Tests for one bounded model-output repair call."""

from __future__ import annotations

import json
import unittest

from claim_agent.agent.action_parser import (
    RuntimeActionType,
    parse_model_action,
)
from claim_agent.agent.repair_execution import (
    RepairModelBoundaryError,
    execute_repair_model_call,
)
from claim_agent.agent.runtime_budget import (
    RuntimeBudgetExceededError,
    RuntimeBudgetLimits,
    RuntimeBudgetUsage,
)
from claim_agent.agent.runtime_state import AgentRuntimeState, RuntimePhase
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace
from claim_agent.agent.scripted_model import (
    ScriptedModelClient,
    ScriptedModelOutput,
)
from tests.agent.runtime_test_support import (
    make_test_agent_decision_payload,
)


def make_repair_limits(
    max_repair_attempts: int = 1,
) -> RuntimeBudgetLimits:
    """Create compact limits for repair tests."""

    return RuntimeBudgetLimits(
        max_model_calls=3,
        max_tool_calls=2,
        max_repair_attempts=max_repair_attempts,
        max_total_steps=4,
    )


def make_planning_state() -> AgentRuntimeState:
    """Create a runtime state from which malformed output may be repaired."""

    return AgentRuntimeState(
        run_id="run-1",
        phase=RuntimePhase.PLANNING,
    )


class RepairExecutionTests(unittest.TestCase):
    """Verify repair budgeting, model requests, parsing, and tracing."""

    def test_repair_call_returns_corrected_action(self) -> None:
        """Malformed output may receive one corrected structured response."""

        corrected_decision_payload = make_test_agent_decision_payload()

        model_client = ScriptedModelClient(
            [
                ScriptedModelOutput(
                    response_text=json.dumps(
                        {
                            "action": "propose_decision",
                            "decision": corrected_decision_payload,
                        }
                    )
                )
            ]
        )
        trace = InMemoryRuntimeTrace(run_id="run-1")

        outcome = execute_repair_model_call(
            model_client=model_client,
            budget_limits=make_repair_limits(),
            budget_usage=RuntimeBudgetUsage(
                model_calls_used=1,
                total_steps_used=1,
            ),
            runtime_state=make_planning_state(),
            runtime_trace=trace,
            system_instructions="Return one permitted JSON action.",
            task_context="Review the synthetic MRI denial.",
            rejected_output='{"action": "propose_decision"',
            problem_messages=("Model output must be valid JSON.",),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        self.assertEqual(
            outcome.runtime_state.phase,
            RuntimePhase.REPAIRING,
        )
        repaired_action = parse_model_action(outcome.repaired_response_text)

        self.assertEqual(
            repaired_action.action_type,
            RuntimeActionType.PROPOSE_DECISION,
        )
        self.assertEqual(
            repaired_action.decision_payload["decision_id"],
            "decision-test-001",
        )
        self.assertEqual(outcome.budget_usage.model_calls_used, 2)
        self.assertEqual(outcome.budget_usage.repair_attempts_used, 1)
        self.assertEqual(outcome.budget_usage.total_steps_used, 2)
        self.assertEqual(
            [entry.event_name for entry in trace.entries],
            [
                "REPAIR_ATTEMPTED",
                "MODEL_REQUESTED",
                "MODEL_RESPONDED",
            ],
        )

    def test_repair_request_contains_rejected_output_and_problem(
        self,
    ) -> None:
        """The model should receive the exact reason its output was rejected."""

        model_client = ScriptedModelClient(
            [
                ScriptedModelOutput(
                    response_text="""
                    {
                        "action": "request_manual_review",
                        "reason": "Evidence remains insufficient."
                    }
                    """
                )
            ]
        )

        execute_repair_model_call(
            model_client=model_client,
            budget_limits=make_repair_limits(),
            budget_usage=RuntimeBudgetUsage(
                model_calls_used=1,
                total_steps_used=1,
            ),
            runtime_state=make_planning_state(),
            runtime_trace=InMemoryRuntimeTrace(run_id="run-1"),
            system_instructions="Return one permitted JSON action.",
            task_context="Review the synthetic MRI denial.",
            rejected_output="not valid JSON",
            problem_messages=("Model output must be valid JSON.",),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        repair_request = model_client.received_requests[0]

        self.assertIn(
            "not valid JSON",
            repair_request.task_context,
        )
        self.assertIn(
            "Model output must be valid JSON.",
            repair_request.task_context,
        )

    def test_exhausted_repair_budget_prevents_model_call(self) -> None:
        """A second malformed response cannot trigger unlimited retries."""

        limits = make_repair_limits(max_repair_attempts=1)
        exhausted_usage = RuntimeBudgetUsage(
            model_calls_used=2,
            repair_attempts_used=1,
            total_steps_used=2,
        )
        model_client = ScriptedModelClient([])
        trace = InMemoryRuntimeTrace(run_id="run-1")

        with self.assertRaisesRegex(
            RuntimeBudgetExceededError,
            "repair_attempt",
        ):
            execute_repair_model_call(
                model_client=model_client,
                budget_limits=limits,
                budget_usage=exhausted_usage,
                runtime_state=make_planning_state(),
                runtime_trace=trace,
                system_instructions="Return one permitted JSON action.",
                task_context="Review the synthetic MRI denial.",
                rejected_output="not valid JSON",
                problem_messages=("Model output must be valid JSON.",),
                allowed_tool_names=("retrieve_medical_policy",),
            )

        self.assertEqual(model_client.received_requests, ())
        self.assertEqual(trace.entries, ())

    def test_model_boundary_error_preserves_repair_state_and_budget(self) -> None:
        """A failed repair request should retain its consumed capacity."""

        class FailingRepairModelClient:
            """Simulate a model provider failing during a repair request."""

            def generate_response(self, request: object) -> object:
                """Raise instead of returning a repaired model response."""

                raise TimeoutError("The synthetic repair request timed out.")

        trace = InMemoryRuntimeTrace(run_id="run-1")

        with self.assertRaises(RepairModelBoundaryError) as raised_error:
            execute_repair_model_call(
                model_client=FailingRepairModelClient(),
                budget_limits=make_repair_limits(),
                budget_usage=RuntimeBudgetUsage(
                    model_calls_used=1,
                    total_steps_used=1,
                ),
                runtime_state=make_planning_state(),
                runtime_trace=trace,
                system_instructions="Return one permitted JSON action.",
                task_context="Review the synthetic MRI denial.",
                rejected_output="not valid JSON",
                problem_messages=("Model output must be valid JSON.",),
                allowed_tool_names=("retrieve_medical_policy",),
            )

        boundary_error = raised_error.exception
        self.assertEqual(
            boundary_error.runtime_state.phase,
            RuntimePhase.REPAIRING,
        )
        self.assertEqual(boundary_error.budget_usage.model_calls_used, 2)
        self.assertEqual(boundary_error.budget_usage.repair_attempts_used, 1)
        self.assertEqual(boundary_error.budget_usage.total_steps_used, 2)
        self.assertIsInstance(boundary_error.original_error, TimeoutError)
        self.assertEqual(
            [entry.event_name for entry in trace.entries],
            ["REPAIR_ATTEMPTED", "MODEL_REQUESTED", "MODEL_FAILED"],
        )


if __name__ == "__main__":
    unittest.main()
