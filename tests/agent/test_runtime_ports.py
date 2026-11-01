"""Tests for temporary runtime integration boundaries."""

from __future__ import annotations

import unittest
from typing import Mapping

from claim_agent.agent.runtime_ports import (
    DecisionValidatorPort,
    OfflineToolObservation,
    OfflineValidationResult,
    OfflineValidationStatus,
    ToolDispatcherPort,
)


class CompatibleDispatcher:
    """Minimal dispatcher used to verify the structural protocol."""

    def dispatch(
        self,
        tool_name: str,
        tool_arguments: Mapping[str, object],
    ) -> OfflineToolObservation:
        """Return a predictable successful observation."""

        return OfflineToolObservation(
            succeeded=True,
            payload={"tool_name": tool_name},
            source_ids=("POL-BCBSAZ-MRI-BRAIN@v1",),
        )


class CompatibleValidator:
    """Minimal validator used to verify the structural protocol."""

    def validate(
        self,
        decision_payload: Mapping[str, object],
    ) -> OfflineValidationResult:
        """Accept the supplied decision for this interface test."""

        return OfflineValidationResult(
            status=OfflineValidationStatus.ACCEPTED,
            validated_decision=decision_payload,
        )


class RuntimePortTests(unittest.TestCase):
    """Verify temporary dispatcher and validator boundary behavior."""

    def test_compatible_dispatcher_satisfies_protocol(self) -> None:
        """The runtime can accept any dispatcher with the required method."""

        self.assertIsInstance(
            CompatibleDispatcher(),
            ToolDispatcherPort,
        )

    def test_compatible_validator_satisfies_protocol(self) -> None:
        """The runtime can accept any validator with the required method."""

        self.assertIsInstance(
            CompatibleValidator(),
            DecisionValidatorPort,
        )

    def test_successful_tool_observation_preserves_sources(self) -> None:
        """Retrieved evidence should retain stable source identifiers."""

        observation = OfflineToolObservation(
            succeeded=True,
            payload={"passage_count": 2},
            source_ids=("POL-BCBSAZ-MRI-BRAIN@v1",),
        )

        self.assertTrue(observation.succeeded)
        self.assertEqual(observation.payload["passage_count"], 2)
        self.assertEqual(
            observation.source_ids,
            ("POL-BCBSAZ-MRI-BRAIN@v1",),
        )

    def test_successful_tool_observation_rejects_error_code(self) -> None:
        """A tool result cannot report success and failure simultaneously."""

        with self.assertRaisesRegex(ValueError, "error_code"):
            OfflineToolObservation(
                succeeded=True,
                error_code="TIMEOUT",
            )

    def test_failed_tool_observation_requires_error_code(self) -> None:
        """The runtime needs an explicit reason for every failed tool call."""

        with self.assertRaisesRegex(ValueError, "error_code"):
            OfflineToolObservation(succeeded=False)

    def test_retryable_tool_failure_is_supported(self) -> None:
        """A temporary timeout may be retried within the remaining budget."""

        observation = OfflineToolObservation(
            succeeded=False,
            error_code="TIMEOUT",
            retryable=True,
        )

        self.assertFalse(observation.succeeded)
        self.assertTrue(observation.retryable)

    def test_accepted_validation_requires_validated_decision(self) -> None:
        """An accepted result must return the decision safe for downstream use."""

        with self.assertRaisesRegex(ValueError, "validated_decision"):
            OfflineValidationResult(
                status=OfflineValidationStatus.ACCEPTED,
            )

    def test_repairable_validation_cannot_return_validated_decision(
        self,
    ) -> None:
        """A decision needing repair is not yet safe for downstream use."""

        with self.assertRaisesRegex(ValueError, "Only an accepted"):
            OfflineValidationResult(
                status=OfflineValidationStatus.REPAIRABLE,
                validated_decision={"disposition": "APPEAL"},
            )

    def test_manual_review_result_preserves_problem_messages(self) -> None:
        """Terminal validation should explain why human review is required."""

        result = OfflineValidationResult(
            status=OfflineValidationStatus.MANUAL_REVIEW,
            problem_messages=("citation does not resolve to retrieved evidence",),
        )

        self.assertEqual(
            result.problem_messages,
            ("citation does not resolve to retrieved evidence",),
        )


if __name__ == "__main__":
    unittest.main()
