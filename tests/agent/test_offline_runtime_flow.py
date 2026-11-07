"""Tests for ordinary offline planning and tool flow."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from claim_agent.agent.model_interface import ModelResponse
from claim_agent.agent.offline_runtime import OfflineAgentRuntime
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


class OfflineRuntimeFlowTests(unittest.TestCase):
    """Verify normal planning, retrieval, completion, and manual review."""

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
            system_instructions=("Retrieve evidence before proposing an appeal."),
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

    def test_failed_tool_observation_can_lead_to_manual_review(
        self,
    ) -> None:
        """Missing policy evidence should remain visible to the next turn."""

        model_client = ScriptedModelClient(
            [
                ScriptedModelOutput(response_text="""
                    {
                        "action": "call_tool",
                        "tool_name": "retrieve_medical_policy",
                        "tool_arguments": {
                            "payer": "UNKNOWN_PAYER",
                            "procedure_code": "70553"
                        }
                    }
                    """),
                ScriptedModelOutput(response_text="""
                    {
                        "action": "request_manual_review",
                        "reason": "No applicable policy evidence was found."
                    }
                    """),
            ]
        )
        tool_dispatcher = ScriptedToolDispatcher(
            [
                ExpectedToolCall(
                    tool_name="retrieve_medical_policy",
                    tool_arguments={
                        "payer": "UNKNOWN_PAYER",
                        "procedure_code": "70553",
                    },
                    observation=OfflineToolObservation(
                        succeeded=False,
                        error_code="NO_MATCH",
                    ),
                )
            ]
        )
        runtime = OfflineAgentRuntime(
            model_client=model_client,
            tool_dispatcher=tool_dispatcher,
            decision_validator=ScriptedDecisionValidator([]),
            budget_limits=make_test_limits(),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        result = runtime.run(
            run_id="run-missing-policy",
            system_instructions="Do not guess missing policy evidence.",
            task_context="Review one synthetic MRI denial.",
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "model_requested_manual_review",
        )
        self.assertIn(
            "NO_MATCH",
            model_client.received_requests[1].task_context,
        )
        self.assertEqual(len(tool_dispatcher.recorded_calls), 1)


if __name__ == "__main__":
    unittest.main()
