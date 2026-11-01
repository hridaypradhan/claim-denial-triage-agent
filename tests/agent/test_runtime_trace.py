"""Tests for the offline runtime's in-memory audit trail."""

from __future__ import annotations

import unittest

from claim_agent.agent.runtime_trace import (
    InMemoryRuntimeTrace,
    RuntimeTraceEntry,
)


class InMemoryRuntimeTraceTests(unittest.TestCase):
    """Verify trace ordering, isolation, and immutability."""

    def test_new_trace_starts_empty(self) -> None:
        """A run should have no recorded activity before work begins."""

        trace = InMemoryRuntimeTrace(run_id="run-1")

        self.assertEqual(trace.run_id, "run-1")
        self.assertEqual(trace.entries, ())

    def test_record_assigns_increasing_sequence_numbers(self) -> None:
        """Events should preserve the exact order in which they occurred."""

        trace = InMemoryRuntimeTrace(run_id="run-1")

        first_entry = trace.record("RUN_STARTED")
        second_entry = trace.record(
            "MODEL_REQUESTED",
            {"request_id": "request-1"},
        )

        self.assertEqual(first_entry.sequence_number, 1)
        self.assertEqual(second_entry.sequence_number, 2)
        self.assertEqual(
            [entry.event_name for entry in trace.entries],
            ["RUN_STARTED", "MODEL_REQUESTED"],
        )

    def test_recorded_details_are_copied_from_caller(self) -> None:
        """Changing the caller's dictionary must not rewrite trace history."""

        event_details = {"tool_name": "retrieve_medical_policy"}
        trace = InMemoryRuntimeTrace(run_id="run-1")

        entry = trace.record("TOOL_REQUESTED", event_details)
        event_details["tool_name"] = "different_tool"

        self.assertEqual(
            entry.details["tool_name"],
            "retrieve_medical_policy",
        )

    def test_recorded_details_cannot_be_modified(self) -> None:
        """A trace entry should behave like append-only audit history."""

        trace = InMemoryRuntimeTrace(run_id="run-1")
        entry = trace.record(
            "TOOL_RETURNED",
            {"status": "OK"},
        )

        with self.assertRaises(TypeError):
            entry.details["status"] = "ERROR"

    def test_separate_runs_have_independent_sequences(self) -> None:
        """Each claim run should begin its own sequence at one."""

        first_trace = InMemoryRuntimeTrace(run_id="run-1")
        second_trace = InMemoryRuntimeTrace(run_id="run-2")

        first_trace.record("RUN_STARTED")
        first_trace.record("MODEL_REQUESTED")
        second_run_entry = second_trace.record("RUN_STARTED")

        self.assertEqual(second_run_entry.sequence_number, 1)
        self.assertEqual(second_run_entry.run_id, "run-2")

    def test_blank_event_name_is_rejected(self) -> None:
        """An unnamed event would be unusable during failure analysis."""

        trace = InMemoryRuntimeTrace(run_id="run-1")

        with self.assertRaisesRegex(ValueError, "event_name"):
            trace.record("   ")

    def test_trace_entry_requires_positive_sequence_number(self) -> None:
        """Sequence numbers begin at one and cannot be zero or negative."""

        with self.assertRaisesRegex(ValueError, "sequence_number"):
            RuntimeTraceEntry(
                run_id="run-1",
                sequence_number=0,
                event_name="RUN_STARTED",
                details={},
            )


if __name__ == "__main__":
    unittest.main()
