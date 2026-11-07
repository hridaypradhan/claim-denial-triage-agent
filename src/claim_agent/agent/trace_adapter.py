"""Translate internal runtime entries into the accepted trace contract."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

from claim_agent.agent.canonical_trace import (
    TraceComponent,
    TraceEvent,
    TraceEventType,
)
from claim_agent.agent.run_configuration import (
    AgentRunConfig,
    calculate_run_configuration_sha256,
)
from claim_agent.agent.runtime_trace import RuntimeTraceEntry
from claim_agent.contracts import RouteDecision
from claim_agent.agent.trace_redaction import redact_trace_payload

TimestampProvider = Callable[[], str]


def current_utc_timestamp() -> str:
    """Return a human-readable UTC timestamp for one canonical event."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class CanonicalTraceAdapter:
    """Convert one completed internal trace into evaluation-facing events."""

    def __init__(
        self,
        run_configuration: AgentRunConfig,
        route_decision: RouteDecision,
        timestamp_provider: TimestampProvider = current_utc_timestamp,
        evaluation_context: Mapping[str, object] | None = None,
    ) -> None:
        """Store the frozen run metadata shared by the canonical events."""

        self._run_configuration = run_configuration
        self._route_decision = route_decision
        self._timestamp_provider = timestamp_provider
        self._evaluation_context = (
            None if evaluation_context is None else dict(evaluation_context)
        )

    def adapt(
        self,
        runtime_entries: Sequence[RuntimeTraceEntry],
    ) -> tuple[TraceEvent, ...]:
        """Convert supported entries and assign gap-free canonical sequences."""

        canonical_events: list[TraceEvent] = []

        for runtime_entry in runtime_entries:
            if runtime_entry.event_name == "PHASE_CHANGED":
                # Phase changes are already implied by the surrounding
                # model, tool, validation, and terminal events.
                continue

            next_sequence_number = len(canonical_events) + 1
            canonical_event = self._adapt_entry(
                runtime_entry=runtime_entry,
                sequence_number=next_sequence_number,
            )
            canonical_events.append(canonical_event)

        return tuple(canonical_events)

    def _adapt_entry(
        self,
        runtime_entry: RuntimeTraceEntry,
        sequence_number: int,
    ) -> TraceEvent:
        """Convert one currently supported internal event."""

        if runtime_entry.event_name == "RUN_STARTED":
            return self._adapt_run_started(
                runtime_entry=runtime_entry,
                sequence_number=sequence_number,
            )

        raise ValueError(
            "Canonical trace adapter does not yet support internal event "
            f"{runtime_entry.event_name}"
        )

    def _adapt_run_started(
        self,
        runtime_entry: RuntimeTraceEntry,
        sequence_number: int,
    ) -> TraceEvent:
        """Build the canonical run header and reproducibility metadata."""

        configuration_payload = self._run_configuration.model_dump(mode="json")
        route_payload = {
            "contract_version": self._route_decision.contract_version,
            "claim_id": self._route_decision.claim_id,
            "service_line_id": self._route_decision.service_line_id,
            "outcome": self._route_decision.outcome.value,
            "reason_code": self._route_decision.reason_code,
            "authorized_tools": list(self._route_decision.authorized_tools),
        }
        payload: dict[str, object] = {
            "config": configuration_payload,
            "config_sha256": calculate_run_configuration_sha256(
                self._run_configuration
            ),
            "route": route_payload,
        }

        if self._evaluation_context is not None:
            payload["evaluation_context"] = dict(self._evaluation_context)

        case_id = (
            f"{self._route_decision.claim_id}"
            f"-L{self._route_decision.service_line_id}"
        )

        return TraceEvent(
            run_id=runtime_entry.run_id,
            case_id=case_id,
            sequence_number=sequence_number,
            event_type=TraceEventType.RUN_STARTED,
            component=TraceComponent.AGENT_RUNTIME,
            timestamp=self._timestamp_provider(),
            payload=redact_trace_payload(payload),
        )
