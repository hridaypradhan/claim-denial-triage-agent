"""Tests for offline output repair and decision revalidation."""

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


class OfflineRuntimeRepairTests(unittest.TestCase):
    """Verify bounded correction of malformed or rejected decisions."""

    def test_malformed_model_output_receives_one_repair(self) -> None:
        """Invalid JSON may receive one budgeted correction attempt."""

        model_client = ScriptedModelClient(
            [
                ScriptedModelOutput(response_text='{"action": "propose_decision"'),
                ScriptedModelOutput(response_text="""
                    {
                        "action": "propose_decision",
                        "decision": {
                            "recommended_disposition": "MANUAL_REVIEW",
                            "rationale": "Required evidence is missing."
                        }
                    }
                    """),
            ]
        )
        decision_validator = ScriptedDecisionValidator(
            [
                OfflineValidationResult(
                    status=OfflineValidationStatus.ACCEPTED,
                    validated_decision={
                        "recommended_disposition": "MANUAL_REVIEW",
                        "rationale": "Required evidence is missing.",
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
            run_id="run-3",
            system_instructions="Return valid structured output.",
            task_context="Review one synthetic claim.",
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.COMPLETED,
        )
        self.assertEqual(result.budget_usage.model_calls_used, 2)
        self.assertEqual(result.budget_usage.repair_attempts_used, 1)
        self.assertEqual(result.budget_usage.total_steps_used, 2)
        self.assertIn(
            "REPAIR_ATTEMPTED",
            [entry.event_name for entry in result.trace_entries],
        )

    def test_invalid_repair_output_stops_for_manual_review(self) -> None:
        """A second malformed response must not trigger another repair."""

        model_client = ScriptedModelClient(
            [
                ScriptedModelOutput(response_text='{"action": "propose_decision"'),
                ScriptedModelOutput(response_text='{"action": "propose_decision"'),
            ]
        )
        runtime = OfflineAgentRuntime(
            model_client=model_client,
            tool_dispatcher=ScriptedToolDispatcher([]),
            decision_validator=ScriptedDecisionValidator([]),
            budget_limits=make_test_limits(),
            allowed_tool_names=("retrieve_medical_policy",),
        )

        result = runtime.run(
            run_id="run-invalid-repair",
            system_instructions="Return valid structured output.",
            task_context="Review one synthetic claim.",
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "repair_output_invalid",
        )
        self.assertEqual(result.budget_usage.model_calls_used, 2)
        self.assertEqual(result.budget_usage.repair_attempts_used, 1)
        self.assertEqual(len(model_client.received_requests), 2)

    def test_repairable_decision_can_be_corrected_and_accepted(
        self,
    ) -> None:
        """One corrected decision should receive deterministic revalidation."""

        model_client = ScriptedModelClient(
            [
                ScriptedModelOutput(response_text="""
                    {
                        "action": "propose_decision",
                        "decision": {
                            "recommended_disposition": "APPEAL",
                            "rationale": "The denial should be appealed."
                        }
                    }
                    """),
                ScriptedModelOutput(response_text="""
                    {
                        "action": "propose_decision",
                        "decision": {
                            "recommended_disposition": "APPEAL",
                            "rationale": "Policy evidence supports an appeal.",
                            "citation_ids": [
                                "POL-BCBSAZ-MRI-BRAIN@v1"
                            ]
                        }
                    }
                    """),
            ]
        )
        decision_validator = ScriptedDecisionValidator(
            [
                OfflineValidationResult(
                    status=OfflineValidationStatus.REPAIRABLE,
                    problem_messages=("An appeal requires at least one citation.",),
                ),
                OfflineValidationResult(
                    status=OfflineValidationStatus.ACCEPTED,
                    validated_decision={
                        "recommended_disposition": "APPEAL",
                        "rationale": ("Policy evidence supports an appeal."),
                        "citation_ids": ["POL-BCBSAZ-MRI-BRAIN@v1"],
                    },
                ),
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
            run_id="run-repairable-decision",
            system_instructions=("Cite retrieved evidence for every appeal."),
            task_context=("Review a synthetic MRI medical-necessity denial."),
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.COMPLETED,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "decision_accepted",
        )
        self.assertEqual(result.budget_usage.model_calls_used, 2)
        self.assertEqual(result.budget_usage.repair_attempts_used, 1)
        self.assertEqual(len(decision_validator.received_decisions), 2)
        self.assertNotIn(
            "citation_ids",
            decision_validator.received_decisions[0],
        )
        self.assertEqual(
            decision_validator.received_decisions[1]["citation_ids"],
            ["POL-BCBSAZ-MRI-BRAIN@v1"],
        )
        self.assertIn(
            "An appeal requires at least one citation.",
            model_client.received_requests[1].task_context,
        )
        self.assertIn(
            "recommended_disposition",
            model_client.received_requests[1].task_context,
        )
        self.assertIn(
            "APPEAL",
            model_client.received_requests[1].task_context,
        )

    def test_second_repairable_result_stops_without_another_call(
        self,
    ) -> None:
        """A rejected correction must not start an unbounded repair loop."""

        model_client = ScriptedModelClient(
            [
                ScriptedModelOutput(response_text="""
                    {
                        "action": "propose_decision",
                        "decision": {
                            "recommended_disposition": "APPEAL"
                        }
                    }
                    """),
                ScriptedModelOutput(response_text="""
                    {
                        "action": "propose_decision",
                        "decision": {
                            "recommended_disposition": "APPEAL",
                            "citation_ids": ["UNKNOWN-SOURCE"]
                        }
                    }
                    """),
            ]
        )
        decision_validator = ScriptedDecisionValidator(
            [
                OfflineValidationResult(
                    status=OfflineValidationStatus.REPAIRABLE,
                    problem_messages=("An appeal requires a valid citation.",),
                ),
                OfflineValidationResult(
                    status=OfflineValidationStatus.REPAIRABLE,
                    problem_messages=("The corrected citation is not recognized.",),
                ),
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
            run_id="run-repair-limit",
            system_instructions=("Use only citations already present in the evidence."),
            task_context=("Review a synthetic MRI medical-necessity denial."),
        )

        self.assertEqual(
            result.final_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            result.final_state.terminal_reason,
            "repair_budget_exhausted",
        )
        self.assertEqual(result.budget_usage.model_calls_used, 2)
        self.assertEqual(result.budget_usage.repair_attempts_used, 1)
        self.assertEqual(len(model_client.received_requests), 2)
        self.assertEqual(len(decision_validator.received_decisions), 2)
        self.assertEqual(
            "repair_attempt",
            result.trace_entries[-1].details["budget_name"],
        )


if __name__ == "__main__":
    unittest.main()
