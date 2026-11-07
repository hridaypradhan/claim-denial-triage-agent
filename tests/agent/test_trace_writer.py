"""Tests for append-only canonical trace persistence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from claim_agent.agent.canonical_trace import (
    TerminalReason,
    TraceComponent,
    TraceEvent,
    TraceEventType,
)
from claim_agent.agent.trace_writer import (
    JsonLinesTraceWriter,
    TraceValidationError,
)


def make_run_started(sequence_number: int = 1) -> TraceEvent:
    """Create a minimal canonical run header for writer tests."""

    return TraceEvent(
        run_id="run-1",
        case_id="claim-1-L1",
        sequence_number=sequence_number,
        event_type=TraceEventType.RUN_STARTED,
        component=TraceComponent.AGENT_RUNTIME,
        timestamp="2026-11-07T15:00:00.000Z",
        payload={},
    )


def make_run_completed(sequence_number: int = 2) -> TraceEvent:
    """Create a minimal validated terminal event for writer tests."""

    return TraceEvent(
        run_id="run-1",
        case_id="claim-1-L1",
        sequence_number=sequence_number,
        event_type=TraceEventType.RUN_COMPLETED,
        component=TraceComponent.AGENT_RUNTIME,
        timestamp="2026-11-07T15:00:01.000Z",
        payload={},
        elapsed_ms=1000,
        terminal_reason=TerminalReason.COMPLETED_VALIDATED,
    )


class JsonLinesTraceWriterTests(unittest.TestCase):
    """Verify complete, immutable, one-file-per-run persistence."""

    def test_complete_trace_is_written_as_json_lines(self) -> None:
        """Each event should occupy one parseable UTF-8 line."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            writer = JsonLinesTraceWriter(Path(temporary_directory))

            trace_path = writer.write(
                [
                    make_run_started(),
                    make_run_completed(),
                ]
            )

            lines = trace_path.read_text(encoding="utf-8").splitlines()
            parsed_events = [json.loads(line) for line in lines]

            self.assertEqual(len(parsed_events), 2)
            self.assertEqual(
                parsed_events[0]["event_type"],
                "RUN_STARTED",
            )
            self.assertEqual(
                parsed_events[-1]["event_type"],
                "RUN_COMPLETED",
            )
            self.assertNotIn(
                "request_id",
                parsed_events[0],
            )

    def test_existing_trace_is_never_overwritten(self) -> None:
        """A second write for one run must preserve its original history."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            writer = JsonLinesTraceWriter(Path(temporary_directory))
            events = [
                make_run_started(),
                make_run_completed(),
            ]
            trace_path = writer.write(events)
            original_contents = trace_path.read_text(encoding="utf-8")

            with self.assertRaises(FileExistsError):
                writer.write(events)

            self.assertEqual(
                trace_path.read_text(encoding="utf-8"),
                original_contents,
            )

    def test_sequence_gap_is_rejected(self) -> None:
        """Persistence must not hide a missing audit event."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            writer = JsonLinesTraceWriter(Path(temporary_directory))

            with self.assertRaisesRegex(
                TraceValidationError,
                "contain no gaps",
            ):
                writer.write(
                    [
                        make_run_started(),
                        make_run_completed(sequence_number=3),
                    ]
                )

    def test_terminal_event_must_be_last(self) -> None:
        """No event may be appended after a run has terminated."""

        trailing_event = TraceEvent(
            run_id="run-1",
            case_id="claim-1-L1",
            sequence_number=3,
            event_type=TraceEventType.RUN_STARTED,
            component=TraceComponent.AGENT_RUNTIME,
            timestamp="2026-11-07T15:00:02.000Z",
            payload={},
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            writer = JsonLinesTraceWriter(Path(temporary_directory))

            with self.assertRaisesRegex(
                TraceValidationError,
                "terminal event must be the final",
            ):
                writer.write(
                    [
                        make_run_started(),
                        make_run_completed(),
                        trailing_event,
                    ]
                )

    def test_unsafe_run_id_is_rejected(self) -> None:
        """A run identifier must not escape the trace output directory."""

        unsafe_start = make_run_started().model_copy(update={"run_id": "../outside"})
        unsafe_end = make_run_completed().model_copy(update={"run_id": "../outside"})

        with tempfile.TemporaryDirectory() as temporary_directory:
            writer = JsonLinesTraceWriter(Path(temporary_directory))

            with self.assertRaisesRegex(
                TraceValidationError,
                "unsafe for a trace filename",
            ):
                writer.write([unsafe_start, unsafe_end])
