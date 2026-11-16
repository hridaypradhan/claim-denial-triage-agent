"""Tests for authorization-first grounded drafting execution."""

from __future__ import annotations

import unittest

from claim_agent.agent.drafting.execution import execute_drafting
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace
from claim_agent.contracts import DraftingOutcome, VerificationOutcome
from claim_agent.safety.drafting import (
    DRAFTING_REASON_INVALID_OUTPUT,
    DRAFTING_REASON_NOT_VERIFIED,
    DRAFTING_REASON_PROVIDER_FAILURE,
    DRAFTING_REASON_TIMEOUT,
)
from tests.agent.drafting.support import (
    FakeDraftingClient,
    StepClock,
    load_draft_output,
    load_drafting_input,
)

REQUEST_ID = "run-005097-01:draft:1"


def runtime_trace() -> InMemoryRuntimeTrace:
    """Return a fresh required audit sink for one drafting attempt."""

    return InMemoryRuntimeTrace(run_id=load_drafting_input().run_id)


class DraftingExecutionTests(unittest.TestCase):
    """Verify successful execution and every fail-closed boundary."""

    def test_grounded_output_is_validated_and_remains_pending_review(self) -> None:
        client = FakeDraftingClient(response_text=load_draft_output())

        result = execute_drafting(
            drafting_client=client,
            request_id=REQUEST_ID,
            drafting_input=load_drafting_input(),
            monotonic_clock=StepClock(2.0, 2.125),
            runtime_trace=runtime_trace(),
        )

        self.assertEqual(DraftingOutcome.ACCEPTED, result.validation_result.outcome)
        self.assertEqual("PENDING_REVIEW", result.validation_result.approval_state)
        self.assertIsNotNone(result.validation_result.validated_draft)
        self.assertEqual(125, result.elapsed_ms)
        self.assertEqual(1, len(client.received_requests))

    def test_unauthorized_input_never_calls_model(self) -> None:
        drafting_input = load_drafting_input()
        rejected_verifier = drafting_input.verifier_result.model_copy(
            update={
                "outcome": VerificationOutcome.UNCERTAIN,
                "requires_manual_review": True,
                "reason_code": "VERIFIER_UNCERTAIN",
            }
        )
        client = FakeDraftingClient(response_text=load_draft_output())

        result = execute_drafting(
            drafting_client=client,
            request_id=REQUEST_ID,
            drafting_input=drafting_input.model_copy(
                update={"verifier_result": rejected_verifier}
            ),
            runtime_trace=runtime_trace(),
        )

        self.assertEqual(
            DraftingOutcome.REJECTED_UNAUTHORIZED,
            result.validation_result.outcome,
        )
        self.assertEqual(
            DRAFTING_REASON_NOT_VERIFIED,
            result.validation_result.reason_code,
        )
        self.assertIsNone(result.request)
        self.assertEqual([], client.received_requests)

    def test_malformed_output_fails_closed(self) -> None:
        result = execute_drafting(
            drafting_client=FakeDraftingClient(response_text="not-json"),
            request_id=REQUEST_ID,
            drafting_input=load_drafting_input(),
            runtime_trace=runtime_trace(),
        )

        self.assertEqual(
            DraftingOutcome.INVALID_OUTPUT,
            result.validation_result.outcome,
        )
        self.assertEqual(
            DRAFTING_REASON_INVALID_OUTPUT,
            result.validation_result.reason_code,
        )

    def test_timeout_and_provider_failure_are_typed(self) -> None:
        cases = (
            (
                TimeoutError("provider timed out"),
                DraftingOutcome.TIMEOUT,
                DRAFTING_REASON_TIMEOUT,
            ),
            (
                RuntimeError("provider unavailable"),
                DraftingOutcome.PROVIDER_FAILURE,
                DRAFTING_REASON_PROVIDER_FAILURE,
            ),
        )

        for error, expected_outcome, expected_reason in cases:
            with self.subTest(error_type=type(error).__name__):
                result = execute_drafting(
                    drafting_client=FakeDraftingClient(error=error),
                    request_id=REQUEST_ID,
                    drafting_input=load_drafting_input(),
                    runtime_trace=runtime_trace(),
                )
                self.assertEqual(expected_outcome, result.validation_result.outcome)
                self.assertEqual(expected_reason, result.validation_result.reason_code)

    def test_response_identity_mismatch_fails_closed(self) -> None:
        result = execute_drafting(
            drafting_client=FakeDraftingClient(
                response_text=load_draft_output(),
                response_request_id="different-request",
            ),
            request_id=REQUEST_ID,
            drafting_input=load_drafting_input(),
            runtime_trace=runtime_trace(),
        )

        self.assertEqual(
            DraftingOutcome.PROVIDER_FAILURE,
            result.validation_result.outcome,
        )
        self.assertEqual(
            DRAFTING_REASON_PROVIDER_FAILURE,
            result.validation_result.reason_code,
        )


if __name__ == "__main__":
    unittest.main()
