"""Canonical trace adaptation tests for verifier orchestration."""

from __future__ import annotations

import unittest

from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace
from claim_agent.agent.verification.execution import execute_verification
from tests.agent.test_trace_adapter import make_adapter
from tests.agent.verification.support import (
    REQUEST_ID,
    FakeVerifierClient,
    StepClock,
    accepted_validation,
    load_output,
    load_verifier_input,
)


class VerifierTraceAdapterTests(unittest.TestCase):
    """Verify that internal verifier events remain visible canonically."""

    def test_successful_verification_adapts_all_stages(self) -> None:
        """Request, response, and outcome should retain verifier identity."""

        verifier_input = load_verifier_input()
        runtime_trace = InMemoryRuntimeTrace(run_id=verifier_input.run_id)

        execute_verification(
            verifier_client=FakeVerifierClient(
                response_text=load_output("valid_agree_output.json")
            ),
            request_id=REQUEST_ID,
            proposed_decision=verifier_input.proposed_decision,
            evidence_bundle=verifier_input.evidence_bundle,
            deterministic_validation=accepted_validation(verifier_input),
            monotonic_clock=StepClock(10.0, 10.125),
            runtime_trace=runtime_trace,
        )

        events = make_adapter().adapt(runtime_trace.entries)

        self.assertEqual(
            [
                "MODEL_REQUESTED",
                "MODEL_RESPONDED",
                "VALIDATION_RETURNED",
            ],
            [event.event_type.value for event in events],
        )

        requested, responded, returned = events

        self.assertEqual("verifier-v1", requested.prompt_version)
        self.assertEqual("VERIFIER", requested.payload["model_role"])
        self.assertEqual(
            verifier_input.bundle_fingerprint,
            requested.payload["bundle_fingerprint"],
        )

        self.assertEqual("VERIFIER", responded.payload["model_role"])
        self.assertEqual(
            "VERIFIER_REPORT",
            responded.payload["response_type"],
        )
        self.assertEqual("AGREE", responded.payload["outcome"])
        self.assertNotIn("raw_output", responded.payload)

        self.assertEqual("VERIFIER", returned.payload["model_role"])
        self.assertEqual("AGREE", returned.payload["outcome"])
        self.assertFalse(returned.payload["requires_manual_review"])
        self.assertEqual(
            verifier_input.bundle_fingerprint,
            returned.payload["bundle_fingerprint"],
        )
        self.assertEqual(125, returned.payload["elapsed_ms"])

    def test_verifier_failure_retains_role_and_sanitized_type(
        self,
    ) -> None:
        """Provider failure should remain distinguishable from planning."""

        verifier_input = load_verifier_input()
        runtime_trace = InMemoryRuntimeTrace(run_id=verifier_input.run_id)

        execute_verification(
            verifier_client=FakeVerifierClient(
                error=RuntimeError("private provider detail")
            ),
            request_id=REQUEST_ID,
            proposed_decision=verifier_input.proposed_decision,
            evidence_bundle=verifier_input.evidence_bundle,
            deterministic_validation=accepted_validation(verifier_input),
            monotonic_clock=StepClock(20.0, 20.050),
            runtime_trace=runtime_trace,
        )

        events = make_adapter().adapt(runtime_trace.entries)

        self.assertEqual(
            [
                "MODEL_REQUESTED",
                "MODEL_FAILED",
                "VALIDATION_RETURNED",
            ],
            [event.event_type.value for event in events],
        )
        self.assertEqual("VERIFIER", events[1].payload["model_role"])
        self.assertEqual(
            "RuntimeError",
            events[1].payload["error_type"],
        )
        self.assertNotIn("private", str(events[1].payload))
        self.assertTrue(events[2].payload["requires_manual_review"])


if __name__ == "__main__":
    unittest.main()
