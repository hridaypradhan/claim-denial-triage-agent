"""Append-only JSON Lines persistence for canonical runtime traces."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Sequence

from claim_agent.agent.canonical_trace import (
    TraceEvent,
    TraceEventType,
)

SAFE_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
TERMINAL_EVENT_TYPES = frozenset(
    {
        TraceEventType.RUN_COMPLETED,
        TraceEventType.MANUAL_REVIEW,
    }
)


class TraceValidationError(ValueError):
    """Raised when events do not form one complete canonical run."""


def validate_complete_trace(events: Sequence[TraceEvent]) -> None:
    """Require one ordered run with exactly one final terminal event."""

    if not events:
        raise TraceValidationError("a trace must contain at least one event")

    expected_sequence_numbers = list(range(1, len(events) + 1))
    actual_sequence_numbers = [event.sequence_number for event in events]
    if actual_sequence_numbers != expected_sequence_numbers:
        raise TraceValidationError(
            "trace sequence numbers must start at one and contain no gaps"
        )

    run_ids = {event.run_id for event in events}
    if len(run_ids) != 1:
        raise TraceValidationError("every trace event must use the same run_id")

    case_ids = {event.case_id for event in events}
    if len(case_ids) != 1:
        raise TraceValidationError("every trace event must use the same case_id")

    if events[0].event_type is not TraceEventType.RUN_STARTED:
        raise TraceValidationError("the first trace event must be RUN_STARTED")

    terminal_events = [
        event for event in events if event.event_type in TERMINAL_EVENT_TYPES
    ]
    if len(terminal_events) != 1:
        raise TraceValidationError("a trace must contain exactly one terminal event")

    if events[-1] is not terminal_events[0]:
        raise TraceValidationError("the terminal event must be the final trace event")


class JsonLinesTraceWriter:
    """Write one immutable UTF-8 JSON Lines file per completed run."""

    def __init__(self, output_directory: Path) -> None:
        """Store the local directory used for generated trace artifacts."""

        self._output_directory = output_directory

    def write(self, events: Sequence[TraceEvent]) -> Path:
        """Validate and exclusively create one run's trace file."""

        validate_complete_trace(events)

        run_id = events[0].run_id
        if not SAFE_RUN_ID_PATTERN.fullmatch(run_id):
            raise TraceValidationError(
                "run_id contains characters unsafe for a trace filename"
            )

        serialized_lines = [
            json.dumps(
                event.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            for event in events
        ]

        self._output_directory.mkdir(
            parents=True,
            exist_ok=True,
        )
        trace_path = self._output_directory / f"{run_id}.jsonl"

        # Exclusive creation is the append-only guarantee: an existing audit
        # artifact is never truncated, edited, or silently replaced.
        with trace_path.open(
            mode="x",
            encoding="utf-8",
            newline="\n",
        ) as trace_file:
            for serialized_line in serialized_lines:
                trace_file.write(serialized_line)
                trace_file.write("\n")

        return trace_path
