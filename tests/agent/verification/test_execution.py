"""Offline tests for tool-less verifier input and execution."""

from __future__ import annotations

import unittest

from claim_agent.agent.verification.execution import (
    build_verifier_input,
    execute_verification,
)
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace
from claim_agent.contracts import VerificationOutcome
from claim_agent.safety.verifier import (
    VERIFIER_REASON_AGREED,
    VERIFIER_REASON_DISAGREED,
    VERIFIER_REASON_INVALID_OUTPUT,
    VERIFIER_REASON_PROVIDER_FAILURE,
    VERIFIER_REASON_TIMEOUT,
    VERIFIER_REASON_UNCERTAIN,
)
from tests.agent.verification.support import (
    REQUEST_ID,
    FakeVerifierClient,
    StepClock,
    accepted_validation,
    load_output,
    load_verifier_input,
)


class VerifierExecutionTests(unittest.TestCase):
    """Verify construction, execution, and fail-safe outcomes."""

    def test_builds_toolless_input_from_decision_and_bundle(self) -> None:
        """The execution input should reuse the exact frozen bundle."""

        source_input = load_verifier_input()

        built_input = build_verifier_input(
            proposed_decision=source_input.proposed_decision,
            evidence_bundle=source_input.evidence_bundle,
        )

        self.assertEqual([], built_input.authorized_tools)
        self.assertEqual(
            source_input.proposed_decision,
            built_input.proposed_decision,
        )
        self.assertIs(
            source_input.evidence_bundle,
            built_input.evidence_bundle,
        )
        self.assertEqual(
            source_input.bundle_fingerprint,
            built_input.bundle_fingerprint,
        )

    def test_agree_disagree_and_uncertain_are_validated(self) -> None:
        """Every model outcome should cross deterministic validation."""

        scenarios = (
            (
                "valid_agree_output.json",
                VerificationOutcome.AGREE,
                VERIFIER_REASON_AGREED,
                False,
            ),
            (
                "disagree_output.json",
                VerificationOutcome.DISAGREE,
                VERIFIER_REASON_DISAGREED,
                True,
            ),
            (
                "uncertain_output.json",
                VerificationOutcome.UNCERTAIN,
                VERIFIER_REASON_UNCERTAIN,
                True,
            ),
        )

        for (
            fixture_name,
            expected_outcome,
            expected_reason,
            expected_manual_review,
        ) in scenarios:
            with self.subTest(fixture=fixture_name):
                verifier_input = load_verifier_input()
                client = FakeVerifierClient(
                    response_text=load_output(fixture_name)
                )

                result = execute_verification(
                    verifier_client=client,
                    request_id=REQUEST_ID,
                    proposed_decision=verifier_input.proposed_decision,
                    evidence_bundle=verifier_input.evidence_bundle,
                    deterministic_validation=accepted_validation(
                        verifier_input
                    ),
                    runtime_trace=InMemoryRuntimeTrace(
                        run_id=verifier_input.run_id
                    ),
                    monotonic_clock=StepClock(10.0, 10.125),
                )

                self.assertEqual(
                    expected_outcome,
                    result.validation_result.outcome,
                )
                self.assertEqual(
                    expected_reason,
                    result.validation_result.reason_code,
                )
                self.assertEqual(
                    expected_manual_review,
                    result.validation_result.requires_manual_review,
                )
                self.assertEqual(125, result.elapsed_ms)
                self.assertEqual(1, len(client.received_requests))
                self.assertEqual(
                    REQUEST_ID,
                    client.received_requests[0].request_id,
                )
                self.assertIsNotNone(result.response)

    def test_malformed_output_fails_to_manual_review(self) -> None:
        """Unparseable provider output must never approve a decision."""

        verifier_input = load_verifier_input()
        client = FakeVerifierClient(response_text="{not valid json")

        result = execute_verification(
            verifier_client=client,
            request_id=REQUEST_ID,
            proposed_decision=verifier_input.proposed_decision,
            evidence_bundle=verifier_input.evidence_bundle,
            deterministic_validation=accepted_validation(verifier_input),
            runtime_trace=InMemoryRuntimeTrace(
                run_id=verifier_input.run_id
            ),
        )

        self.assertEqual(
            VerificationOutcome.INVALID_OUTPUT,
            result.validation_result.outcome,
        )
        self.assertEqual(
            VERIFIER_REASON_INVALID_OUTPUT,
            result.validation_result.reason_code,
        )
        self.assertTrue(result.validation_result.requires_manual_review)

    def test_timeout_fails_to_manual_review(self) -> None:
        """A timeout should become a typed fail-safe validation result."""

        verifier_input = load_verifier_input()
        client = FakeVerifierClient(
            error=TimeoutError("provider exceeded timeout")
        )

        result = execute_verification(
            verifier_client=client,
            request_id=REQUEST_ID,
            proposed_decision=verifier_input.proposed_decision,
            evidence_bundle=verifier_input.evidence_bundle,
            deterministic_validation=accepted_validation(verifier_input),
            runtime_trace=InMemoryRuntimeTrace(
                run_id=verifier_input.run_id
            ),
        )

        self.assertEqual(
            VerificationOutcome.TIMEOUT,
            result.validation_result.outcome,
        )
        self.assertEqual(
            VERIFIER_REASON_TIMEOUT,
            result.validation_result.reason_code,
        )
        self.assertTrue(result.validation_result.requires_manual_review)
        self.assertIsNone(result.response)

    def test_provider_failure_fails_to_manual_review(self) -> None:
        """A provider exception should be sanitized and fail closed."""

        verifier_input = load_verifier_input()
        client = FakeVerifierClient(
            error=RuntimeError("secret provider details")
        )

        result = execute_verification(
            verifier_client=client,
            request_id=REQUEST_ID,
            proposed_decision=verifier_input.proposed_decision,
            evidence_bundle=verifier_input.evidence_bundle,
            deterministic_validation=accepted_validation(verifier_input),
            runtime_trace=InMemoryRuntimeTrace(
                run_id=verifier_input.run_id
            ),
        )

        self.assertEqual(
            VerificationOutcome.PROVIDER_FAILURE,
            result.validation_result.outcome,
        )
        self.assertEqual(
            VERIFIER_REASON_PROVIDER_FAILURE,
            result.validation_result.reason_code,
        )
        self.assertTrue(result.validation_result.requires_manual_review)
        self.assertIsNone(result.response)

        messages = [
            problem.message
            for problem in result.validation_result.validation_problems
        ]
        self.assertNotIn("secret provider details", " ".join(messages))
        self.assertIn("RuntimeError", " ".join(messages))


if __name__ == "__main__":
    unittest.main()
