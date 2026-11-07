"""Canonical event envelope for persisted Milestone 2 traces."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TraceEventType(str, Enum):
    """Events recognized by the accepted trace contract."""

    RUN_STARTED = "RUN_STARTED"
    MODEL_REQUESTED = "MODEL_REQUESTED"
    MODEL_RESPONDED = "MODEL_RESPONDED"
    MODEL_OUTPUT_REJECTED = "MODEL_OUTPUT_REJECTED"
    MODEL_FAILED = "MODEL_FAILED"
    REPAIR_ATTEMPTED = "REPAIR_ATTEMPTED"
    TOOL_REQUESTED = "TOOL_REQUESTED"
    TOOL_RETURNED = "TOOL_RETURNED"
    TOOL_FAILED = "TOOL_FAILED"
    DECISION_PROPOSED = "DECISION_PROPOSED"
    VALIDATION_RETURNED = "VALIDATION_RETURNED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    RUN_COMPLETED = "RUN_COMPLETED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class TraceComponent(str, Enum):
    """Runtime boundary responsible for emitting an event."""

    AGENT_RUNTIME = "AGENT_RUNTIME"
    MODEL_CLIENT = "MODEL_CLIENT"
    TOOL_DISPATCHER = "TOOL_DISPATCHER"
    SAFETY_VALIDATOR = "SAFETY_VALIDATOR"


EVENTS_REQUIRING_REQUEST_ID = frozenset(
    {
        TraceEventType.MODEL_REQUESTED,
        TraceEventType.MODEL_RESPONDED,
        TraceEventType.MODEL_OUTPUT_REJECTED,
        TraceEventType.MODEL_FAILED,
        TraceEventType.REPAIR_ATTEMPTED,
        TraceEventType.TOOL_REQUESTED,
        TraceEventType.TOOL_RETURNED,
        TraceEventType.TOOL_FAILED,
    }
)

TERMINAL_EVENT_TYPES = frozenset(
    {
        TraceEventType.RUN_COMPLETED,
        TraceEventType.MANUAL_REVIEW,
    }
)


EVENTS_REQUIRING_MODEL_ID = frozenset(
    {
        TraceEventType.MODEL_REQUESTED,
        TraceEventType.MODEL_RESPONDED,
    }
)

EVENTS_REQUIRING_ELAPSED_TIME = frozenset(
    {
        TraceEventType.MODEL_RESPONDED,
        TraceEventType.MODEL_FAILED,
        TraceEventType.TOOL_RETURNED,
        TraceEventType.TOOL_FAILED,
        TraceEventType.RUN_COMPLETED,
        TraceEventType.MANUAL_REVIEW,
    }
)


EXPECTED_COMPONENT_BY_EVENT = {
    TraceEventType.RUN_STARTED: TraceComponent.AGENT_RUNTIME,
    TraceEventType.MODEL_REQUESTED: TraceComponent.MODEL_CLIENT,
    TraceEventType.MODEL_RESPONDED: TraceComponent.MODEL_CLIENT,
    TraceEventType.MODEL_OUTPUT_REJECTED: TraceComponent.AGENT_RUNTIME,
    TraceEventType.MODEL_FAILED: TraceComponent.MODEL_CLIENT,
    TraceEventType.REPAIR_ATTEMPTED: TraceComponent.AGENT_RUNTIME,
    TraceEventType.TOOL_REQUESTED: TraceComponent.TOOL_DISPATCHER,
    TraceEventType.TOOL_RETURNED: TraceComponent.TOOL_DISPATCHER,
    TraceEventType.TOOL_FAILED: TraceComponent.TOOL_DISPATCHER,
    TraceEventType.DECISION_PROPOSED: TraceComponent.AGENT_RUNTIME,
    TraceEventType.VALIDATION_RETURNED: TraceComponent.SAFETY_VALIDATOR,
    TraceEventType.VALIDATION_FAILED: TraceComponent.SAFETY_VALIDATOR,
    TraceEventType.RUN_COMPLETED: TraceComponent.AGENT_RUNTIME,
    TraceEventType.MANUAL_REVIEW: TraceComponent.AGENT_RUNTIME,
}


class TerminalReason(str, Enum):
    """Accepted reasons why an agent run reached a terminal state."""

    COMPLETED_VALIDATED = "COMPLETED_VALIDATED"
    VALIDATOR_REQUIRED_REVIEW = "VALIDATOR_REQUIRED_REVIEW"
    MODEL_REQUESTED_REVIEW = "MODEL_REQUESTED_REVIEW"
    MODEL_CALL_BUDGET_EXHAUSTED = "MODEL_CALL_BUDGET_EXHAUSTED"
    TOOL_CALL_BUDGET_EXHAUSTED = "TOOL_CALL_BUDGET_EXHAUSTED"
    REPAIR_BUDGET_EXHAUSTED = "REPAIR_BUDGET_EXHAUSTED"
    STEP_BUDGET_EXHAUSTED = "STEP_BUDGET_EXHAUSTED"
    RUN_TIMEOUT = "RUN_TIMEOUT"
    REPAIR_OUTPUT_INVALID = "REPAIR_OUTPUT_INVALID"
    INVALID_REPAIR_ACTION = "INVALID_REPAIR_ACTION"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    MODEL_REQUEST_TIMEOUT = "MODEL_REQUEST_TIMEOUT"
    MODEL_BOUNDARY_ERROR = "MODEL_BOUNDARY_ERROR"
    TOOL_BOUNDARY_ERROR = "TOOL_BOUNDARY_ERROR"
    VALIDATOR_BOUNDARY_ERROR = "VALIDATOR_BOUNDARY_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"


class UsageStatus(str, Enum):
    """Whether the model provider supplied token-usage information."""

    REPORTED = "REPORTED"
    NOT_REPORTED_BY_PROVIDER = "NOT_REPORTED_BY_PROVIDER"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class TraceEvent(BaseModel):
    """One ordered, evaluation-facing event from an agent run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_version: Literal["0.2"] = "0.2"
    run_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    sequence_number: int = Field(gt=0)
    event_type: TraceEventType
    component: TraceComponent
    timestamp: str = Field(min_length=1)
    payload: dict[str, object]
    request_id: str | None = None
    terminal_reason: TerminalReason | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    usage_status: UsageStatus | None = None

    @model_validator(mode="after")
    def require_expected_component(self) -> "TraceEvent":
        """Ensure each event is attributed to its owning boundary."""

        expected_component = EXPECTED_COMPONENT_BY_EVENT[self.event_type]
        if self.component != expected_component:
            raise ValueError(
                f"{self.event_type.value} must use component "
                f"{expected_component.value}"
            )

        return self

    @model_validator(mode="after")
    def require_model_telemetry_fields(self) -> "TraceEvent":
        """Require model identity, timing, and usage on applicable events."""

        if self.event_type in EVENTS_REQUIRING_MODEL_ID:
            if self.model_id is None or not self.model_id.strip():
                raise ValueError(
                    f"{self.event_type.value} requires a non-empty model_id"
                )
        elif self.model_id is not None:
            raise ValueError(f"{self.event_type.value} must not include model_id")

        if self.event_type == TraceEventType.MODEL_REQUESTED:
            if self.prompt_version is None or not self.prompt_version.strip():
                raise ValueError("MODEL_REQUESTED requires a non-empty prompt_version")
        elif self.prompt_version is not None:
            raise ValueError(f"{self.event_type.value} must not include prompt_version")

        if self.event_type in EVENTS_REQUIRING_ELAPSED_TIME:
            if self.elapsed_ms is None:
                raise ValueError(f"{self.event_type.value} requires elapsed_ms")
        elif self.elapsed_ms is not None:
            raise ValueError(f"{self.event_type.value} must not include elapsed_ms")

        if self.event_type == TraceEventType.MODEL_RESPONDED:
            if self.usage_status is None:
                raise ValueError("MODEL_RESPONDED requires usage_status")

            if self.usage_status == UsageStatus.REPORTED:
                if self.input_tokens is None or self.output_tokens is None:
                    raise ValueError(
                        "REPORTED usage requires input_tokens and output_tokens"
                    )
            elif self.input_tokens is not None or self.output_tokens is not None:
                raise ValueError("unreported usage must not include token counts")
        elif (
            self.usage_status is not None
            or self.input_tokens is not None
            or self.output_tokens is not None
        ):
            raise ValueError(
                f"{self.event_type.value} must not include model usage fields"
            )

        return self

    @model_validator(mode="after")
    def require_conditional_envelope_fields(self) -> "TraceEvent":
        """Require identifiers and terminal reasons only where they apply."""

        if self.event_type in EVENTS_REQUIRING_REQUEST_ID:
            if self.request_id is None or not self.request_id.strip():
                raise ValueError(
                    f"{self.event_type.value} requires a non-empty request_id"
                )
        elif self.request_id is not None:
            raise ValueError(f"{self.event_type.value} must not include request_id")

        if self.event_type in TERMINAL_EVENT_TYPES:
            if self.terminal_reason is None:
                raise ValueError(f"{self.event_type.value} requires terminal_reason")
        elif self.terminal_reason is not None:
            raise ValueError(
                f"{self.event_type.value} must not include terminal_reason"
            )

        return self
