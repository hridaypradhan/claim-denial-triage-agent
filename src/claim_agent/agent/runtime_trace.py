"""Temporary in-memory audit trail for the offline agent runtime.

This module implements trace ordering and storage without defining the team's
final shared TraceEvent contract. A later adapter will translate these internal
entries into the schema approved through issue #7.
"""

from __future__ import annotations

# TODO(#7): Add an adapter that converts these internal entries into the
# approved TraceEvent schema and append-only JSON Lines storage format.

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class RuntimeTraceEntry:
    """One ordered observation about an agent run.

    Attributes:
        run_id: Identifier shared by all entries from one runtime execution.
        sequence_number: One-based position of this entry in the run.
        event_name: Readable description of what occurred.
        details: Small event-specific values needed for debugging or evaluation.
            The mapping is copied and made read-only when the entry is created.
    """

    run_id: str
    sequence_number: int
    event_name: str
    details: Mapping[str, object]

    def __post_init__(self) -> None:
        """Validate the entry and protect its details from later mutation."""

        if not self.run_id.strip():
            raise ValueError("run_id must contain a non-whitespace value")

        if self.sequence_number <= 0:
            raise ValueError("sequence_number must be greater than zero")

        if not self.event_name.strip():
            raise ValueError("event_name must contain a non-whitespace value")

        # A model, tool, or caller may reuse its original dictionary elsewhere.
        # Copying it here ensures later changes cannot rewrite trace history.
        protected_details = MappingProxyType(dict(self.details))
        object.__setattr__(self, "details", protected_details)


class InMemoryRuntimeTrace:
    """Record ordered trace entries for one offline agent run.

    The recorder is intentionally in memory for the first vertical slice.
    Persistence as JSON Lines belongs behind the final trace-contract adapter.
    """

    def __init__(self, run_id: str) -> None:
        """Create an empty trace associated with one run identifier."""

        if not run_id.strip():
            raise ValueError("run_id must contain a non-whitespace value")

        self._run_id = run_id
        self._entries: list[RuntimeTraceEntry] = []

    @property
    def run_id(self) -> str:
        """Return the run identifier attached to every trace entry."""

        return self._run_id

    @property
    def entries(self) -> Tuple[RuntimeTraceEntry, ...]:
        """Return an immutable view of the entries recorded so far."""

        return tuple(self._entries)

    def record(
        self,
        event_name: str,
        details: Optional[Mapping[str, object]] = None,
    ) -> RuntimeTraceEntry:
        """Append one event using the next deterministic sequence number.

        Args:
            event_name: Readable internal event name.
            details: Optional event-specific metadata. The mapping is copied so
                callers cannot alter an event after it has been recorded.

        Returns:
            The newly recorded immutable RuntimeTraceEntry.
        """

        next_sequence_number = len(self._entries) + 1
        entry = RuntimeTraceEntry(
            run_id=self._run_id,
            sequence_number=next_sequence_number,
            event_name=event_name,
            details={} if details is None else details,
        )
        self._entries.append(entry)

        return entry
