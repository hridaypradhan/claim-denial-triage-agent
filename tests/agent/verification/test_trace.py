"""Trace tests for tool-less verifier orchestration."""

from __future__ import annotations

import unittest
from typing import Mapping

from claim_agent.agent.verification.execution import execute_verification
from claim_agent.contracts import VerificationOutcome
from tests.agent.verification.support import (
    REQUEST_ID,
    FakeVerifierClient,
    StepClock,
    accepted_validation,
    load_output,
    load_verifier_input,
)


class RecordingRuntimeTrace:
    """Record internal events without requiring a complete agent run."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def record(
        self,
        event_name: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Store a defensive copy of one event."""

        self.events.append(
            (
                event_name,
                {} if details is None else dict(details),
            )
        )


class VerifierTraceTests(unittest.TestCase):
    """Verify request, response, outcome, latency, and failure tracing."""

    def test_success_records_request_response_and_outcome(self) -> None:
        """A completed verifier call should emit all audit stages."""

        verifier_input = load_verifier_input()
        trace = RecordingRuntimeTrace()

        result = execute_verification(
            verifier_client=FakeVerifierClient(
                response_text=load_output("valid_agree_output.json")
            ),
            request_id=REQUEST_ID,
            proposed_decision=verifier_input.proposed_decision,
            evidence_bundle=verifier_input.evidence_bundle,
            deterministic_validation=accepted_validation(verifier_input),
            monotonic_clock=StepClock(10.0, 10.125),
            runtime_trace=trace,
        )

        self.assertEqual(
            [
                "MODEL_REQUESTED",
                "MODEL_RESPONDED",
                "VERIFICATION_RETURNED",
            ],
            [event_name for event_name, _ in trace.events],
        )

        requested = trace.events[0][1]
        self.assertEqual(REQUEST_ID, requested["request_id"])
        self.assertEqual("VERIFIER", requested["model_role"])
        self.assertEqual("verifier-v1", requested["prompt_version"])
        self.assertEqual(
            verifier_input.bundle_fingerprint,
            requested["bundle_fingerprint"],
        )
        self.assertEqual(64, len(requested["prompt_sha256"]))

        responded = trace.events[1][1]
        self.assertEqual(REQUEST_ID, responded["request_id"])
        self.assertEqual("VERIFIER", responded["model_role"])
        self.assertEqual(
            "fake-provider-request",
            responded["provider_request_id"],
        )
        self.assertEqual(200, responded["input_tokens"])
        self.assertEqual(30, responded["output_tokens"])
        self.assertEqual(125, responded["elapsed_ms"])

        returned = trace.events[2][1]
        self.assertEqual(
            VerificationOutcome.AGREE.value,
            returned["outcome"],
        )
        self.assertFalse(returned["requires_manual_review"])
        self.assertEqual(
            verifier_input.run_id,
            returned["evidence_run_id"],
        )
        self.assertEqual(
            verifier_input.bundle_fingerprint,
            returned["bundle_fingerprint"],
        )
        self.assertEqual(
            result.validation_result.reason_code,
            returned["reason_code"],
        )

    def test_timeout_and_provider_failure_record_failure(self) -> None:
        """Boundary failures should record sanitized failure metadata."""

        scenarios = (
            (
                TimeoutError("private timeout detail"),
                "REQUEST_TIMEOUT",
                "TimeoutError",
            ),
            (
                RuntimeError("private provider detail"),
                "BOUNDARY_EXCEPTION",
                "RuntimeError",
            ),
        )

        for error, failure_class, error_type in scenarios:
            with self.subTest(error_type=error_type):
                verifier_input = load_verifier_input()
                trace = RecordingRuntimeTrace()

                execute_verification(
                    verifier_client=FakeVerifierClient(error=error),
                    request_id=REQUEST_ID,
                    proposed_decision=(verifier_input.proposed_decision),
                    evidence_bundle=verifier_input.evidence_bundle,
                    deterministic_validation=accepted_validation(verifier_input),
                    monotonic_clock=StepClock(20.0, 20.050),
                    runtime_trace=trace,
                )

                self.assertEqual(
                    [
                        "MODEL_REQUESTED",
                        "MODEL_FAILED",
                        "VERIFICATION_RETURNED",
                    ],
                    [event_name for event_name, _ in trace.events],
                )

                failed = trace.events[1][1]
                self.assertEqual("VERIFIER", failed["model_role"])
                self.assertEqual(failure_class, failed["failure_class"])
                self.assertEqual(error_type, failed["error_type"])
                self.assertFalse(failed["retryable"])
                self.assertEqual(50, failed["elapsed_ms"])
                self.assertNotIn(
                    "private",
                    str(failed),
                )

                returned = trace.events[2][1]
                self.assertTrue(returned["requires_manual_review"])


if __name__ == "__main__":
    unittest.main()
