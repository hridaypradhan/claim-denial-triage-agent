"""Tests for deterministic offline dispatcher and validator stand-ins."""

from __future__ import annotations

import unittest

from claim_agent.agent.runtime_ports import (
    DecisionValidatorPort,
    OfflineToolObservation,
    OfflineValidationResult,
    OfflineValidationStatus,
    ToolDispatcherPort,
)
from claim_agent.agent.scripted_ports import (
    ExpectedToolCall,
    ScriptedBoundaryExhaustedError,
    ScriptedDecisionValidator,
    ScriptedToolCallMismatchError,
    ScriptedToolDispatcher,
)


def make_successful_policy_observation() -> OfflineToolObservation:
    """Create a small policy result for dispatcher tests."""

    return OfflineToolObservation(
        succeeded=True,
        payload={"criteria_formula": "A AND B AND (C OR D)"},
        source_ids=("POL-BCBSAZ-MRI-BRAIN@v1",),
    )


def make_expected_policy_call() -> ExpectedToolCall:
    """Describe the policy lookup expected from the runtime."""

    return ExpectedToolCall(
        tool_name="retrieve_medical_policy",
        tool_arguments={
            "payer": "BCBS_AZ",
            "procedure_code": "70553",
        },
        observation=make_successful_policy_observation(),
    )


class ScriptedToolDispatcherTests(unittest.TestCase):
    """Verify scripted tool-call matching and recording."""

    def test_scripted_dispatcher_satisfies_dispatcher_protocol(self) -> None:
        """The stand-in can be supplied wherever the runtime expects a port."""

        dispatcher = ScriptedToolDispatcher([])

        self.assertIsInstance(dispatcher, ToolDispatcherPort)

    def test_matching_call_returns_scripted_observation(self) -> None:
        """The expected MRI-policy lookup should receive its policy result."""

        dispatcher = ScriptedToolDispatcher([make_expected_policy_call()])

        observation = dispatcher.dispatch(
            "retrieve_medical_policy",
            {
                "payer": "BCBS_AZ",
                "procedure_code": "70553",
            },
        )

        self.assertTrue(observation.succeeded)
        self.assertEqual(
            observation.payload["criteria_formula"],
            "A AND B AND (C OR D)",
        )
        self.assertEqual(dispatcher.remaining_call_count, 0)

    def test_matching_call_is_recorded(self) -> None:
        """The test can inspect exactly what the runtime requested."""

        dispatcher = ScriptedToolDispatcher([make_expected_policy_call()])

        dispatcher.dispatch(
            "retrieve_medical_policy",
            {
                "payer": "BCBS_AZ",
                "procedure_code": "70553",
            },
        )

        self.assertEqual(len(dispatcher.recorded_calls), 1)
        self.assertEqual(
            dispatcher.recorded_calls[0].tool_name,
            "retrieve_medical_policy",
        )
        self.assertEqual(
            dispatcher.recorded_calls[0].tool_arguments["payer"],
            "BCBS_AZ",
        )

    def test_wrong_tool_name_is_rejected_without_consuming_script(
        self,
    ) -> None:
        """The runtime cannot silently substitute a different evidence tool."""

        dispatcher = ScriptedToolDispatcher([make_expected_policy_call()])

        with self.assertRaisesRegex(
            ScriptedToolCallMismatchError,
            "retrieve_medical_policy",
        ):
            dispatcher.dispatch(
                "get_encounter_note",
                {
                    "payer": "BCBS_AZ",
                    "procedure_code": "70553",
                },
            )

        self.assertEqual(dispatcher.remaining_call_count, 1)
        self.assertEqual(dispatcher.recorded_calls, ())

    def test_wrong_tool_arguments_are_rejected(self) -> None:
        """A policy lookup using the wrong procedure code must fail."""

        dispatcher = ScriptedToolDispatcher([make_expected_policy_call()])

        with self.assertRaisesRegex(
            ScriptedToolCallMismatchError,
            "70553",
        ):
            dispatcher.dispatch(
                "retrieve_medical_policy",
                {
                    "payer": "BCBS_AZ",
                    "procedure_code": "70552",
                },
            )

    def test_call_after_script_exhaustion_is_rejected(self) -> None:
        """Unexpected extra retrieval indicates incorrect runtime behavior."""

        dispatcher = ScriptedToolDispatcher([])

        with self.assertRaises(ScriptedBoundaryExhaustedError):
            dispatcher.dispatch(
                "retrieve_medical_policy",
                {
                    "payer": "BCBS_AZ",
                    "procedure_code": "70553",
                },
            )


class ScriptedDecisionValidatorTests(unittest.TestCase):
    """Verify scripted validation order and decision recording."""

    def test_scripted_validator_satisfies_validator_protocol(self) -> None:
        """The stand-in can be supplied wherever the runtime expects a port."""

        validator = ScriptedDecisionValidator([])

        self.assertIsInstance(validator, DecisionValidatorPort)

    def test_validator_returns_results_in_scripted_order(self) -> None:
        """A repairable result may be followed by an accepted result."""

        validator = ScriptedDecisionValidator(
            [
                OfflineValidationResult(
                    status=OfflineValidationStatus.REPAIRABLE,
                    problem_messages=("citation_ids is required",),
                ),
                OfflineValidationResult(
                    status=OfflineValidationStatus.ACCEPTED,
                    validated_decision={"recommended_disposition": "MANUAL_REVIEW"},
                ),
            ]
        )

        first_result = validator.validate({"recommended_disposition": "APPEAL"})
        second_result = validator.validate({"recommended_disposition": "MANUAL_REVIEW"})

        self.assertEqual(
            first_result.status,
            OfflineValidationStatus.REPAIRABLE,
        )
        self.assertEqual(
            second_result.status,
            OfflineValidationStatus.ACCEPTED,
        )
        self.assertEqual(validator.remaining_result_count, 0)

    def test_validator_records_received_decisions(self) -> None:
        """Tests can inspect each decision sent for deterministic validation."""

        validator = ScriptedDecisionValidator(
            [
                OfflineValidationResult(
                    status=OfflineValidationStatus.MANUAL_REVIEW,
                    problem_messages=("citation could not be resolved",),
                )
            ]
        )

        validator.validate({"recommended_disposition": "APPEAL"})

        self.assertEqual(len(validator.received_decisions), 1)
        self.assertEqual(
            validator.received_decisions[0]["recommended_disposition"],
            "APPEAL",
        )

    def test_validation_after_script_exhaustion_is_rejected(self) -> None:
        """An unexpected validation call should fail the offline scenario."""

        validator = ScriptedDecisionValidator([])

        with self.assertRaises(ScriptedBoundaryExhaustedError):
            validator.validate({"recommended_disposition": "APPEAL"})


if __name__ == "__main__":
    unittest.main()
