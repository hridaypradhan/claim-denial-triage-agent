"""Tests for the first offline runtime-loop slice."""

from __future__ import annotations

import unittest

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


def make_test_limits() -> RuntimeBudgetLimits:
    """Provide compact limits for first-pass runtime tests."""

    return RuntimeBudgetLimits(
        max_model_calls=2,
        max_tool_calls=2,
        max_repair_attempts=1,
        max_total_steps=3,
    )


class OfflineAgentRuntimeTests(unittest.TestCase):
    """Verify completion and conservative stopping behavior."""

    def test_accepted_decision_completes_run(self) -> None:
        """A validated MRI decision should reach successful completion."""

        model_client = ScriptedModelClient([ScriptedModelOutput(response_text="""
                    {
                        "action": "propose_decision",
                        "decision": {
                            "recommended_disposition": "MANUAL_REVIEW",
                            "rationale": "Evidence remains incomplete."
                        }
                    }
                    """)])
        decision_validator = ScriptedDecisionValidator(
            [
                OfflineValidationResult(
                    status=OfflineValidationStatus.ACCEPTED,
                    validated_decision={
                        "recommended_disposition": "MANUAL_REVIEW",
                        "rationale": "Evidence remains incomplete.",
                    },
                )
            ]
        )
        runtime = OfflineAgentRuntime(
            model_client=model_client,
            tool_dispatcher=ScriptedToolDispatcher([]),
            decision_validator=decision_validator,
            budget_limits=make_test_limits(),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        result = runtime.run(
            run_id="run-1",
            system_instructions=(
                "Use only supplied evidence and request manual review "
                "when evidence is insufficient."
            ),
            task_context=("BCBS_AZ denied CPT 70553 for medical necessity."),
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.COMPLETED,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "decision_accepted",
        )
        self.assertEqual(
            result.validated_decision["recommended_disposition"],
            "MANUAL_REVIEW",
        )
        self.assertEqual(result.budget_usage.model_calls_used, 1)
        self.assertEqual(result.budget_usage.total_steps_used, 1)
        self.assertEqual(
            [entry.event_name for entry in result.trace_entries],
            [
                "RUN_STARTED",
                "PHASE_CHANGED",
                "MODEL_REQUESTED",
                "MODEL_RESPONDED",
                "DECISION_PROPOSED",
                "VALIDATION_RETURNED",
                "RUN_COMPLETED",
            ],
        )

    def test_model_can_request_manual_review(self) -> None:
        """The model may stop when required evidence is unavailable."""

        model_client = ScriptedModelClient([ScriptedModelOutput(response_text="""
                    {
                        "action": "request_manual_review",
                        "reason": "The applicable policy was not found."
                    }
                    """)])
        decision_validator = ScriptedDecisionValidator([])
        runtime = OfflineAgentRuntime(
            model_client=model_client,
            tool_dispatcher=ScriptedToolDispatcher([]),
            decision_validator=decision_validator,
            budget_limits=make_test_limits(),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        result = runtime.run(
            run_id="run-2",
            system_instructions="Do not guess missing payer policy.",
            task_context="No applicable policy is available.",
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "model_requested_manual_review",
        )
        self.assertIsNone(result.validated_decision)
        self.assertEqual(
            decision_validator.received_decisions,
            (),
        )
        self.assertEqual(
            result.trace_entries[-1].event_name,
            "MANUAL_REVIEW",
        )

    def test_malformed_model_output_stops_safely(self) -> None:
        """Invalid JSON must not be interpreted as an executable action."""

        model_client = ScriptedModelClient(
            [ScriptedModelOutput(response_text='{"action": "propose_decision"')]
        )
        decision_validator = ScriptedDecisionValidator([])
        runtime = OfflineAgentRuntime(
            model_client=model_client,
            tool_dispatcher=ScriptedToolDispatcher([]),
            decision_validator=decision_validator,
            budget_limits=make_test_limits(),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        result = runtime.run(
            run_id="run-3",
            system_instructions="Return valid structured output.",
            task_context="Review one synthetic claim.",
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "model_output_invalid",
        )
        self.assertEqual(result.budget_usage.model_calls_used, 1)
        self.assertEqual(
            result.trace_entries[-1].details["terminal_reason"],
            "model_output_invalid",
        )

    def test_model_request_receives_runtime_tool_allowlist(self) -> None:
        """Only configured read-only tools should be exposed to the model."""

        model_client = ScriptedModelClient([ScriptedModelOutput(response_text="""
                    {
                        "action": "request_manual_review",
                        "reason": "No evidence has been retrieved yet."
                    }
                    """)])
        runtime = OfflineAgentRuntime(
            model_client=model_client,
            tool_dispatcher=ScriptedToolDispatcher([]),
            decision_validator=ScriptedDecisionValidator([]),
            budget_limits=make_test_limits(),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        runtime.run(
            run_id="run-4",
            system_instructions="Use only allowlisted tools.",
            task_context="Review one synthetic MRI denial.",
        )

        self.assertEqual(
            model_client.received_requests[0].allowed_tool_names,
            ("retrieve_medical_policy",),
        )

    def test_tool_result_is_used_in_next_planning_turn(self) -> None:
        """The model should receive retrieved policy data before deciding."""

        model_client = ScriptedModelClient(
            [
                ScriptedModelOutput(response_text="""
                    {
                        "action": "call_tool",
                        "tool_name": "retrieve_medical_policy",
                        "tool_arguments": {
                            "payer": "BCBS_AZ",
                            "procedure_code": "70553"
                        }
                    }
                    """),
                ScriptedModelOutput(response_text="""
                    {
                        "action": "propose_decision",
                        "decision": {
                            "recommended_disposition": "APPEAL",
                            "citation_ids": [
                                "POL-BCBSAZ-MRI-BRAIN@v1"
                            ]
                        }
                    }
                    """),
            ]
        )
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
                        payload={"criteria_formula": ("A AND B AND (C OR D)")},
                        source_ids=("POL-BCBSAZ-MRI-BRAIN@v1",),
                    ),
                )
            ]
        )
        decision_validator = ScriptedDecisionValidator(
            [
                OfflineValidationResult(
                    status=OfflineValidationStatus.ACCEPTED,
                    validated_decision={
                        "recommended_disposition": "APPEAL",
                        "citation_ids": ["POL-BCBSAZ-MRI-BRAIN@v1"],
                    },
                )
            ]
        )
        runtime = OfflineAgentRuntime(
            model_client=model_client,
            tool_dispatcher=tool_dispatcher,
            decision_validator=decision_validator,
            budget_limits=make_test_limits(),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        result = runtime.run(
            run_id="run-5",
            system_instructions=(
                "Retrieve evidence before proposing an appeal."
            ),
            task_context=("BCBS_AZ denied CPT 70553 for medical necessity."),
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.COMPLETED,
        )
        self.assertEqual(result.budget_usage.model_calls_used, 2)
        self.assertEqual(result.budget_usage.tool_calls_used, 1)
        self.assertEqual(result.budget_usage.total_steps_used, 3)
        self.assertEqual(len(model_client.received_requests), 2)
        self.assertNotIn(
            "BEGIN TOOL OBSERVATION DATA",
            model_client.received_requests[0].task_context,
        )
        self.assertIn(
            "BEGIN TOOL OBSERVATION DATA",
            model_client.received_requests[1].task_context,
        )
        self.assertIn(
            "POL-BCBSAZ-MRI-BRAIN@v1",
            model_client.received_requests[1].task_context,
        )


if __name__ == "__main__":
    unittest.main()
