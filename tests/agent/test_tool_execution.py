"""Tests for one offline runtime tool-execution cycle."""

from __future__ import annotations

import unittest

from claim_agent.agent.action_parser import parse_model_action
from claim_agent.agent.runtime_budget import (
    RuntimeBudgetExceededError,
    RuntimeBudgetLimits,
    RuntimeBudgetUsage,
)
from claim_agent.agent.runtime_ports import OfflineToolObservation
from claim_agent.agent.runtime_state import AgentRuntimeState, RuntimePhase
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace
from claim_agent.agent.scripted_ports import (
    ExpectedToolCall,
    ScriptedToolDispatcher,
)
from claim_agent.agent.tool_execution import (
    ToolNotAllowedError,
    execute_tool_action,
)


def make_limits(
    max_tool_calls: int = 2,
) -> RuntimeBudgetLimits:
    """Create compact limits for tool-execution tests."""

    return RuntimeBudgetLimits(
        max_model_calls=3,
        max_tool_calls=max_tool_calls,
        max_repair_attempts=1,
        max_total_steps=4,
    )


def make_policy_action():
    """Create the MRI-policy request used by these tests."""

    return parse_model_action("""
        {
            "action": "call_tool",
            "tool_name": "retrieve_medical_policy",
            "tool_arguments": {
                "payer": "BCBS_AZ",
                "procedure_code": "70553"
            }
        }
        """)


def make_planning_state() -> AgentRuntimeState:
    """Create a runtime state ready to process a model action."""

    return AgentRuntimeState(
        run_id="run-1",
        phase=RuntimePhase.PLANNING,
    )


class ToolExecutionTests(unittest.TestCase):
    """Verify allowlist, budget, trace, and context behavior."""

    def test_successful_tool_call_returns_to_planning(self) -> None:
        """Retrieved policy evidence should prepare another model turn."""

        observation = OfflineToolObservation(
            succeeded=True,
            payload={"criteria_formula": "A AND B AND (C OR D)"},
            source_ids=("POL-BCBSAZ-MRI-BRAIN@v1",),
        )
        dispatcher = ScriptedToolDispatcher(
            [
                ExpectedToolCall(
                    tool_name="retrieve_medical_policy",
                    tool_arguments={
                        "payer": "BCBS_AZ",
                        "procedure_code": "70553",
                    },
                    observation=observation,
                )
            ]
        )
        trace = InMemoryRuntimeTrace(run_id="run-1")

        outcome = execute_tool_action(
            action=make_policy_action(),
            allowed_tool_names=("retrieve_medical_policy",),
            tool_dispatcher=dispatcher,
            budget_limits=make_limits(),
            budget_usage=RuntimeBudgetUsage(),
            runtime_state=make_planning_state(),
            runtime_trace=trace,
            task_context="Review the synthetic MRI denial.",
        )

        self.assertEqual(
            outcome.runtime_state.phase,
            RuntimePhase.PLANNING,
        )
        self.assertEqual(outcome.budget_usage.tool_calls_used, 1)
        self.assertEqual(outcome.budget_usage.total_steps_used, 1)
        self.assertIn(
            "A AND B AND (C OR D)",
            outcome.task_context,
        )
        self.assertEqual(
            [entry.event_name for entry in trace.entries],
            [
                "TOOL_REQUESTED",
                "TOOL_RETURNED",
                "PHASE_CHANGED",
            ],
        )

    def test_tool_outside_allowlist_is_rejected(self) -> None:
        """The model cannot call a tool the runtime did not expose."""

        dispatcher = ScriptedToolDispatcher([])
        trace = InMemoryRuntimeTrace(run_id="run-1")

        with self.assertRaisesRegex(
            ToolNotAllowedError,
            "retrieve_medical_policy",
        ):
            execute_tool_action(
                action=make_policy_action(),
                allowed_tool_names=("get_encounter_note",),
                tool_dispatcher=dispatcher,
                budget_limits=make_limits(),
                budget_usage=RuntimeBudgetUsage(),
                runtime_state=make_planning_state(),
                runtime_trace=trace,
                task_context="Review the synthetic MRI denial.",
            )

        self.assertEqual(dispatcher.recorded_calls, ())
        self.assertEqual(trace.entries, ())

    def test_exhausted_tool_budget_prevents_dispatch(self) -> None:
        """No retrieval occurs after the tool-call budget is consumed."""

        dispatcher = ScriptedToolDispatcher([])
        trace = InMemoryRuntimeTrace(run_id="run-1")
        limits = make_limits(max_tool_calls=1)
        exhausted_usage = RuntimeBudgetUsage().record_tool_call(limits)

        with self.assertRaisesRegex(
            RuntimeBudgetExceededError,
            "tool_call",
        ):
            execute_tool_action(
                action=make_policy_action(),
                allowed_tool_names=("retrieve_medical_policy",),
                tool_dispatcher=dispatcher,
                budget_limits=limits,
                budget_usage=exhausted_usage,
                runtime_state=make_planning_state(),
                runtime_trace=trace,
                task_context="Review the synthetic MRI denial.",
            )

        self.assertEqual(dispatcher.recorded_calls, ())
        self.assertEqual(trace.entries, ())

    def test_failed_observation_is_added_to_next_context(self) -> None:
        """A timeout should be visible to the next planning turn."""

        failed_observation = OfflineToolObservation(
            succeeded=False,
            error_code="TIMEOUT",
            retryable=True,
        )
        dispatcher = ScriptedToolDispatcher(
            [
                ExpectedToolCall(
                    tool_name="retrieve_medical_policy",
                    tool_arguments={
                        "payer": "BCBS_AZ",
                        "procedure_code": "70553",
                    },
                    observation=failed_observation,
                )
            ]
        )

        outcome = execute_tool_action(
            action=make_policy_action(),
            allowed_tool_names=("retrieve_medical_policy",),
            tool_dispatcher=dispatcher,
            budget_limits=make_limits(),
            budget_usage=RuntimeBudgetUsage(),
            runtime_state=make_planning_state(),
            runtime_trace=InMemoryRuntimeTrace(run_id="run-1"),
            task_context="Review the synthetic MRI denial.",
        )

        self.assertIn('"error_code":"TIMEOUT"', outcome.task_context)
        self.assertIn('"retryable":true', outcome.task_context)


if __name__ == "__main__":
    unittest.main()
