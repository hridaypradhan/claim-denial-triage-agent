"""Canonical trace adaptation tests for grounded drafting."""

from __future__ import annotations

import unittest

from claim_agent.agent.drafting.execution import execute_drafting
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace
from claim_agent.agent.trace_redaction import REDACTED_VALUE
from tests.agent.drafting.support import (
    FakeDraftingClient,
    StepClock,
    load_draft_output,
    load_drafting_input,
)
from tests.agent.test_trace_adapter import make_adapter

REQUEST_ID = "run-005097-01:draft:1"


class DraftingTraceAdapterTests(unittest.TestCase):
    """Verify drafting activity remains visible without draft prose."""

    def test_successful_drafting_adapts_all_stages(self) -> None:
        drafting_input = load_drafting_input()
        trace = InMemoryRuntimeTrace(run_id=drafting_input.run_id)

        execute_drafting(
            drafting_client=FakeDraftingClient(response_text=load_draft_output()),
            request_id=REQUEST_ID,
            drafting_input=drafting_input,
            monotonic_clock=StepClock(10.0, 10.125),
            runtime_trace=trace,
        )

        events = make_adapter().adapt(trace.entries)
        self.assertEqual(
            ["MODEL_REQUESTED", "MODEL_RESPONDED", "VALIDATION_RETURNED"],
            [event.event_type.value for event in events],
        )
        requested, responded, returned = events
        self.assertEqual("DRAFTER", requested.payload["model_role"])
        self.assertEqual("drafting-v1", requested.prompt_version)
        self.assertEqual("DRAFTER", responded.payload["model_role"])
        self.assertEqual("APPEAL_DRAFT", responded.payload["response_type"])
        self.assertNotIn("body", responded.payload)
        self.assertNotIn("subject", responded.payload)
        self.assertEqual("DRAFTER", returned.payload["model_role"])
        self.assertEqual("PENDING_REVIEW", returned.payload["approval_state"])
        self.assertEqual(125, returned.payload["elapsed_ms"])

    def test_failure_retains_role_and_sanitized_type(self) -> None:
        drafting_input = load_drafting_input()
        trace = InMemoryRuntimeTrace(run_id=drafting_input.run_id)

        execute_drafting(
            drafting_client=FakeDraftingClient(
                error=RuntimeError("private provider detail")
            ),
            request_id=REQUEST_ID,
            drafting_input=drafting_input,
            monotonic_clock=StepClock(20.0, 20.050),
            runtime_trace=trace,
        )

        events = make_adapter().adapt(trace.entries)
        self.assertEqual("DRAFTER", events[1].payload["model_role"])
        self.assertEqual("RuntimeError", events[1].payload["error_type"])
        self.assertNotIn("private", str(events[1].payload))
        self.assertTrue(events[2].payload["requires_manual_review"])

    def test_invalid_output_is_redacted_in_canonical_trace(self) -> None:
        """Invalid provider prose should retain only a stable hash."""

        drafting_input = load_drafting_input()
        trace = InMemoryRuntimeTrace(run_id=drafting_input.run_id)
        private_output = "private invalid appeal prose"

        execute_drafting(
            drafting_client=FakeDraftingClient(response_text=private_output),
            request_id=REQUEST_ID,
            drafting_input=drafting_input,
            runtime_trace=trace,
        )

        responded = make_adapter().adapt(trace.entries)[1]
        self.assertEqual(REDACTED_VALUE, responded.payload["raw_output"])
        self.assertEqual(64, len(responded.payload["raw_output_sha256"]))
        self.assertNotIn(private_output, str(responded.payload))


if __name__ == "__main__":
    unittest.main()
