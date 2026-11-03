"""Tests for the first offline runtime-loop slice."""

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
