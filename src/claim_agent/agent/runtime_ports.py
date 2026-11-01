"""Temporary boundaries between the offline runtime and teammate-owned code.

These types support the independent vertical slice while the shared contracts
in issues #3, #5, and #6 remain unresolved. They must be replaced or adapted
when the approved production contracts become available.
"""

from __future__ import annotations

# TODO(#3): Replace OfflineToolObservation with an adapter around the approved
# ToolResult and EvidencePassage contracts.
# TODO(#6): Replace OfflineValidationResult with an adapter around the approved
# AgentDecision and ValidationResult contracts.

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Optional, Protocol, Tuple, runtime_checkable


@dataclass(frozen=True)
class OfflineToolObservation:
    """Temporary result returned by an offline tool dispatcher.

    This type represents only what the runtime needs to continue safely. It is
    not the final ToolResult or EvidencePassage contract owned by the team.
    """

    succeeded: bool
    payload: Mapping[str, object] = field(default_factory=dict)
    source_ids: Tuple[str, ...] = ()
    error_code: Optional[str] = None
    retryable: bool = False

    def __post_init__(self) -> None:
        """Validate success and failure information and protect the payload."""

        object.__setattr__(
            self,
            "payload",
            MappingProxyType(dict(self.payload)),
        )

        if self.succeeded and self.error_code is not None:
            raise ValueError(
                "A successful tool observation cannot contain an error_code"
            )

        if self.succeeded and self.retryable:
            raise ValueError("A successful tool observation cannot be marked retryable")

        if not self.succeeded:
            if self.error_code is None or not self.error_code.strip():
                raise ValueError("A failed tool observation requires an error_code")

        if any(not source_id.strip() for source_id in self.source_ids):
            raise ValueError("source_ids cannot contain blank identifiers")


class OfflineValidationStatus(str, Enum):
    """Temporary outcomes returned by the offline decision validator."""

    ACCEPTED = "ACCEPTED"
    REPAIRABLE = "REPAIRABLE"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass(frozen=True)
class OfflineValidationResult:
    """Temporary validation response consumed by the offline runtime."""

    status: OfflineValidationStatus
    problem_messages: Tuple[str, ...] = ()
    validated_decision: Optional[Mapping[str, object]] = None

    def __post_init__(self) -> None:
        """Ensure accepted and rejected outcomes contain consistent data."""

        has_validated_decision = self.validated_decision is not None

        if self.status is OfflineValidationStatus.ACCEPTED:
            if not has_validated_decision:
                raise ValueError("An accepted result requires validated_decision")
        elif has_validated_decision:
            raise ValueError("Only an accepted result may contain validated_decision")

        if any(not message.strip() for message in self.problem_messages):
            raise ValueError("problem_messages cannot contain blank values")

        if self.validated_decision is not None:
            protected_decision = MappingProxyType(dict(self.validated_decision))
            object.__setattr__(
                self,
                "validated_decision",
                protected_decision,
            )


@runtime_checkable
class ToolDispatcherPort(Protocol):
    """Behavior the runtime requires from an offline or real dispatcher."""

    def dispatch(
        self,
        tool_name: str,
        tool_arguments: Mapping[str, object],
    ) -> OfflineToolObservation:
        """Execute one allowlisted read-only tool request."""


@runtime_checkable
class DecisionValidatorPort(Protocol):
    """Behavior the runtime requires from a decision validator."""

    def validate(
        self,
        decision_payload: Mapping[str, object],
    ) -> OfflineValidationResult:
        """Validate one proposed decision without model involvement."""
