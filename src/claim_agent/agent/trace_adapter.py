"""Translate internal runtime entries into the accepted trace contract."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

from claim_agent.agent.action_parser import (
    ModelOutputParseError,
    RuntimeActionType,
    parse_model_action,
)
from claim_agent.agent.canonical_trace import (
    TerminalReason,
    TraceComponent,
    TraceEvent,
    TraceEventType,
    UsageStatus,
)
from claim_agent.agent.run_configuration import (
    AgentRunConfig,
    calculate_run_configuration_sha256,
)
from claim_agent.agent.runtime_trace import RuntimeTraceEntry
from claim_agent.agent.trace_adapter_helpers import (
    build_manual_review_reasons,
    build_runtime_counters,
    build_validation_payload,
    collected_passage_ids,
    latest_validated_decision,
    next_model_call_index,
)
from claim_agent.agent.trace_redaction import redact_trace_payload
from claim_agent.contracts import RouteDecision

TimestampProvider = Callable[[], str]

TERMINAL_REASON_BY_RUNTIME_REASON = {
    "decision_accepted": TerminalReason.COMPLETED_VALIDATED,
    "decision_requires_manual_review": TerminalReason.VALIDATOR_REQUIRED_REVIEW,
    "model_requested_manual_review": TerminalReason.MODEL_REQUESTED_REVIEW,
    "repair_requested_manual_review": TerminalReason.MODEL_REQUESTED_REVIEW,
    "model_timeout": TerminalReason.MODEL_REQUEST_TIMEOUT,
    "repair_model_timeout": TerminalReason.MODEL_REQUEST_TIMEOUT,
    "model_budget_exhausted": TerminalReason.MODEL_CALL_BUDGET_EXHAUSTED,
    "tool_budget_exhausted": TerminalReason.TOOL_CALL_BUDGET_EXHAUSTED,
    "repair_budget_exhausted": TerminalReason.REPAIR_BUDGET_EXHAUSTED,
    "runtime_timeout": TerminalReason.RUN_TIMEOUT,
    "repair_output_invalid": TerminalReason.REPAIR_OUTPUT_INVALID,
    "invalid_validation_repair_action": TerminalReason.INVALID_REPAIR_ACTION,
    "tool_not_allowed": TerminalReason.TOOL_NOT_ALLOWED,
    "model_boundary_error": TerminalReason.MODEL_BOUNDARY_ERROR,
    "tool_boundary_error": TerminalReason.TOOL_BOUNDARY_ERROR,
    "validation_boundary_error": TerminalReason.VALIDATOR_BOUNDARY_ERROR,
}


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
        """Store frozen metadata shared by every canonical event."""

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
        """Convert every observable entry and assign gap-free sequences."""

        canonical_events: list[TraceEvent] = []
        counters = build_runtime_counters(runtime_entries)

        for entry_index, runtime_entry in enumerate(runtime_entries):
            if runtime_entry.event_name == "PHASE_CHANGED":
                continue

            canonical_events.append(
                self._adapt_entry(
                    runtime_entry=runtime_entry,
                    sequence_number=len(canonical_events) + 1,
                    prior_entries=runtime_entries[:entry_index],
                    counters=counters,
                )
            )

        return tuple(canonical_events)

    def _adapt_entry(
        self,
        runtime_entry: RuntimeTraceEntry,
        sequence_number: int,
        prior_entries: Sequence[RuntimeTraceEntry],
        counters: Mapping[str, int],
    ) -> TraceEvent:
        """Convert one internal event without silently discarding it."""

        if runtime_entry.event_name == "RUN_STARTED":
            return self._adapt_run_started(runtime_entry, sequence_number)

        name = runtime_entry.event_name
        details = dict(runtime_entry.details)
        common = self._common_event_values(runtime_entry, sequence_number)

        if name == "MODEL_REQUESTED":
            return TraceEvent(
                **common,
                event_type=TraceEventType.MODEL_REQUESTED,
                component=TraceComponent.MODEL_CLIENT,
                request_id=str(details["request_id"]),
                model_id=self._run_configuration.model_id,
                prompt_version=self._run_configuration.prompt_version,
                payload=redact_trace_payload(
                    {
                        "call_index": next_model_call_index(prior_entries),
                        "prompt_sha256": details["prompt_sha256"],
                        "passage_ids": collected_passage_ids(prior_entries),
                        "repair_attempt": details.get("repair_attempt", 0),
                    }
                ),
            )

        if name == "MODEL_RESPONDED":
            return self._adapt_model_responded(details, common)

        if name == "MODEL_OUTPUT_REJECTED":
            return TraceEvent(
                **common,
                event_type=TraceEventType.MODEL_OUTPUT_REJECTED,
                component=TraceComponent.AGENT_RUNTIME,
                request_id=str(details["request_id"]),
                payload=redact_trace_payload(
                    {
                        "rejection_code": details["rejection_code"],
                        "schema_error_paths": details["schema_error_paths"],
                    }
                ),
            )

        if name == "MODEL_FAILED":
            return TraceEvent(
                **common,
                event_type=TraceEventType.MODEL_FAILED,
                component=TraceComponent.MODEL_CLIENT,
                request_id=str(details["request_id"]),
                elapsed_ms=int(details.get("elapsed_ms", 0)),
                payload=redact_trace_payload(
                    {
                        "failure_class": details["failure_class"],
                        "error_type": details["error_type"],
                        "retryable": details["retryable"],
                        "repair_attempt": details.get("repair_attempt", 0),
                    }
                ),
            )

        if name == "REPAIR_ATTEMPTED":
            return TraceEvent(
                **common,
                event_type=TraceEventType.REPAIR_ATTEMPTED,
                component=TraceComponent.AGENT_RUNTIME,
                request_id=str(details["request_id"]),
                payload=redact_trace_payload(
                    {
                        "repair_index": details["repair_index"],
                        "repair_kind": details["repair_kind"],
                        "rejected_request_id": details["rejected_request_id"],
                    }
                ),
            )

        if name in {"TOOL_REQUESTED", "TOOL_RETRY_REQUESTED"}:
            return self._adapt_tool_requested(details, common, name)

        if name == "TOOL_RETURNED":
            return TraceEvent(
                **common,
                event_type=TraceEventType.TOOL_RETURNED,
                component=TraceComponent.TOOL_DISPATCHER,
                request_id=str(details["request_id"]),
                elapsed_ms=int(details.get("elapsed_ms", 0)),
                payload=redact_trace_payload(
                    {
                        "tool_name": details["tool_name"],
                        "attempt": details["attempt"],
                        "status": details["status"],
                        "error_code": details["error_code"] or None,
                        "retryable": details["retryable"],
                        "source_ids": details["source_ids"],
                        "passage_ids": details["passage_ids"],
                    }
                ),
            )

        if name == "TOOL_FAILED":
            return TraceEvent(
                **common,
                event_type=TraceEventType.TOOL_FAILED,
                component=TraceComponent.TOOL_DISPATCHER,
                request_id=str(details["request_id"]),
                elapsed_ms=int(details.get("elapsed_ms", 0)),
                payload=redact_trace_payload(details),
            )

        if name == "DECISION_PROPOSED":
            return TraceEvent(
                **common,
                event_type=TraceEventType.DECISION_PROPOSED,
                component=TraceComponent.AGENT_RUNTIME,
                payload=redact_trace_payload(dict(details["decision"])),
            )

        if name == "VALIDATION_RETURNED":
            return TraceEvent(
                **common,
                event_type=TraceEventType.VALIDATION_RETURNED,
                component=TraceComponent.SAFETY_VALIDATOR,
                payload=redact_trace_payload(
                    build_validation_payload(details)
                ),
            )

        if name == "VALIDATION_FAILED":
            return TraceEvent(
                **common,
                event_type=TraceEventType.VALIDATION_FAILED,
                component=TraceComponent.SAFETY_VALIDATOR,
                payload=redact_trace_payload(details),
            )

        if name in {"RUN_COMPLETED", "MANUAL_REVIEW"}:
            return self._adapt_terminal(
                runtime_entry,
                sequence_number,
                prior_entries,
                counters,
            )

        raise ValueError(
            "Canonical trace adapter does not support internal event "
            f"{runtime_entry.event_name}"
        )

    def _adapt_run_started(
        self,
        runtime_entry: RuntimeTraceEntry,
        sequence_number: int,
    ) -> TraceEvent:
        """Build the canonical run header and reproducibility metadata."""

        route_payload = {
            "contract_version": self._route_decision.contract_version,
            "claim_id": self._route_decision.claim_id,
            "service_line_id": self._route_decision.service_line_id,
            "outcome": self._route_decision.outcome.value,
            "reason_code": self._route_decision.reason_code,
            "authorized_tools": list(self._route_decision.authorized_tools),
        }
        payload: dict[str, object] = {
            "config": self._run_configuration.model_dump(mode="json"),
            "config_sha256": calculate_run_configuration_sha256(
                self._run_configuration
            ),
            "route": route_payload,
        }
        if self._evaluation_context is not None:
            payload["evaluation_context"] = dict(self._evaluation_context)

        return TraceEvent(
            **self._common_event_values(runtime_entry, sequence_number),
            event_type=TraceEventType.RUN_STARTED,
            component=TraceComponent.AGENT_RUNTIME,
            payload=redact_trace_payload(payload),
        )

    def _adapt_model_responded(
        self,
        details: Mapping[str, object],
        common: Mapping[str, object],
    ) -> TraceEvent:
        """Describe one model result while redacting unrestricted text."""

        response_text = str(details["response_text"])
        try:
            action = parse_model_action(response_text)
            if action.action_type is RuntimeActionType.CALL_TOOL:
                response_payload: dict[str, object] = {
                    "response_type": "TOOL_CALLS",
                    "tool_calls": [
                        {
                            "tool_name": action.tool_name,
                            "tool_args": dict(action.tool_arguments),
                        }
                    ],
                }
            elif action.action_type is RuntimeActionType.PROPOSE_DECISION:
                response_payload = {
                    "response_type": "DECISION",
                    "decision": dict(action.decision_payload),
                }
            else:
                response_payload = {
                    "response_type": "MANUAL_REVIEW_REQUEST",
                    "manual_review_reasons": [action.manual_review_reason],
                }
        except ModelOutputParseError:
            response_payload = {
                "response_type": "INVALID",
                "raw_output": response_text,
            }

        input_tokens = details.get("input_tokens")
        output_tokens = details.get("output_tokens")
        if input_tokens is not None and output_tokens is not None:
            usage_status = UsageStatus.REPORTED
        elif "scripted" in self._run_configuration.model_provider.lower():
            usage_status = UsageStatus.NOT_APPLICABLE
        else:
            usage_status = UsageStatus.NOT_REPORTED_BY_PROVIDER

        return TraceEvent(
            **common,
            event_type=TraceEventType.MODEL_RESPONDED,
            component=TraceComponent.MODEL_CLIENT,
            request_id=str(details["request_id"]),
            model_id=self._run_configuration.model_id,
            elapsed_ms=int(details.get("elapsed_ms", 0)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_status=usage_status,
            payload=redact_trace_payload(response_payload),
        )

    def _adapt_tool_requested(
        self,
        details: Mapping[str, object],
        common: Mapping[str, object],
        event_name: str,
    ) -> TraceEvent:
        """Convert initial and retry requests to one canonical event type."""

        payload = {
            "tool_call_index": details["tool_call_index"],
            "attempt": details["attempt"],
            "tool_name": details["tool_name"],
            "tool_args": details["tool_args"],
        }
        if event_name == "TOOL_RETRY_REQUESTED":
            payload["retry_of_request_id"] = details["previous_request_id"]

        return TraceEvent(
            **common,
            event_type=TraceEventType.TOOL_REQUESTED,
            component=TraceComponent.TOOL_DISPATCHER,
            request_id=str(details["request_id"]),
            payload=redact_trace_payload(payload),
        )

    def _adapt_terminal(
        self,
        runtime_entry: RuntimeTraceEntry,
        sequence_number: int,
        prior_entries: Sequence[RuntimeTraceEntry],
        counters: Mapping[str, int],
    ) -> TraceEvent:
        """Map terminal reasons and preserve consumed runtime counters."""

        details = dict(runtime_entry.details)
        internal_reason = str(details["terminal_reason"])
        terminal_reason = self._terminal_reason(internal_reason, details)
        is_completed = runtime_entry.event_name == "RUN_COMPLETED"
        payload: dict[str, object] = {
            "approval_state": "PENDING_REVIEW",
            "counters": dict(counters),
        }
        if is_completed:
            decision = latest_validated_decision(prior_entries)
            if not decision:
                raise ValueError(
                    "RUN_COMPLETED requires a preceding validated decision"
                )
            payload.update(
                {
                    "final_disposition": decision["recommended_disposition"],
                    "final_root_cause": decision["root_cause"],
                }
            )
        else:
            payload["manual_review_reasons"] = build_manual_review_reasons(
                terminal_reason=terminal_reason.value,
                prior_entries=prior_entries,
            )
            payload["during_repair"] = internal_reason.startswith("repair_")

        return TraceEvent(
            **self._common_event_values(runtime_entry, sequence_number),
            event_type=(
                TraceEventType.RUN_COMPLETED
                if is_completed
                else TraceEventType.MANUAL_REVIEW
            ),
            component=TraceComponent.AGENT_RUNTIME,
            elapsed_ms=int(
                details.get("elapsed_ms", runtime_entry.elapsed_ms)
            ),
            terminal_reason=terminal_reason,
            payload=redact_trace_payload(payload),
        )

    def _common_event_values(
        self,
        runtime_entry: RuntimeTraceEntry,
        sequence_number: int,
    ) -> dict[str, object]:
        return {
            "run_id": runtime_entry.run_id,
            "case_id": (
                f"{self._route_decision.claim_id}"
                f"-L{self._route_decision.service_line_id}"
            ),
            "sequence_number": sequence_number,
            "timestamp": self._timestamp_provider(),
        }

    @staticmethod
    def _terminal_reason(
        internal_reason: str,
        details: Mapping[str, object],
    ) -> TerminalReason:
        budget_reason_by_name = {
            "model_call": TerminalReason.MODEL_CALL_BUDGET_EXHAUSTED,
            "tool_call": TerminalReason.TOOL_CALL_BUDGET_EXHAUSTED,
            "repair_attempt": TerminalReason.REPAIR_BUDGET_EXHAUSTED,
            "total_step": TerminalReason.STEP_BUDGET_EXHAUSTED,
        }
        budget_name = details.get("budget_name")
        if budget_name is not None:
            try:
                return budget_reason_by_name[str(budget_name)]
            except KeyError as error:
                raise ValueError(
                    f"Unsupported runtime budget name: {budget_name}"
                ) from error
        return TERMINAL_REASON_BY_RUNTIME_REASON.get(
            internal_reason,
            TerminalReason.RUNTIME_ERROR,
        )
