"""Trace tests for grounded drafting orchestration."""

from __future__ import annotations

import unittest
from typing import Mapping

from claim_agent.agent.drafting.execution import execute_drafting
from tests.agent.drafting.support import (
    FakeDraftingClient,
    StepClock,
    load_draft_output,
    load_drafting_input,
)

REQUEST_ID = "run-005097-01:draft:1"


class RecordingRuntimeTrace:
    """Record internal events without requiring a complete agent run."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def record(
        self,
        event_name: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.events.append((event_name, {} if details is None else dict(details)))


class DraftingTraceTests(unittest.TestCase):
    """Verify drafting request, response, outcome, and failure tracing."""

    def test_success_records_request_response_and_outcome(self) -> None:
        trace = RecordingRuntimeTrace()
        drafting_input = load_drafting_input()

        execute_drafting(
            drafting_client=FakeDraftingClient(response_text=load_draft_output()),
            request_id=REQUEST_ID,
            drafting_input=drafting_input,
            monotonic_clock=StepClock(10.0, 10.125),
            runtime_trace=trace,
        )

        self.assertEqual(
            ["MODEL_REQUESTED", "MODEL_RESPONDED", "DRAFTING_RETURNED"],
            [name for name, _ in trace.events],
        )
        requested = trace.events[0][1]
        self.assertEqual("DRAFTER", requested["model_role"])
        self.assertEqual("drafting-v1", requested["prompt_version"])
        self.assertEqual(
            drafting_input.bundle_fingerprint,
            requested["bundle_fingerprint"],
        )
        self.assertEqual(64, len(requested["prompt_sha256"]))

        responded = trace.events[1][1]
        self.assertEqual("DRAFTER", responded["model_role"])
        self.assertEqual(125, responded["elapsed_ms"])

        returned = trace.events[2][1]
        self.assertEqual("ACCEPTED", returned["outcome"])
        self.assertEqual("PENDING_REVIEW", returned["approval_state"])
        self.assertEqual(
            drafting_input.bundle_fingerprint,
            returned["bundle_fingerprint"],
        )

    def test_provider_failure_records_only_sanitized_metadata(self) -> None:
        trace = RecordingRuntimeTrace()

        execute_drafting(
            drafting_client=FakeDraftingClient(
                error=RuntimeError("private provider detail")
            ),
            request_id=REQUEST_ID,
            drafting_input=load_drafting_input(),
            monotonic_clock=StepClock(20.0, 20.050),
            runtime_trace=trace,
        )

        self.assertEqual(
            ["MODEL_REQUESTED", "MODEL_FAILED", "DRAFTING_RETURNED"],
            [name for name, _ in trace.events],
        )
        failed = trace.events[1][1]
        self.assertEqual("DRAFTER", failed["model_role"])
        self.assertEqual("RuntimeError", failed["error_type"])
        self.assertNotIn("private", str(failed))


if __name__ == "__main__":
    unittest.main()
