"""Compatibility boundaries used by the deterministic offline runtime.

These small internal types keep scripted regression tests stable while the
merged production contracts are connected through adapters.
"""

from __future__ import annotations

# TODO(#11, owner=Hriday): After Milestone 2, decide whether replacing the
# internal OfflineToolObservation is worth the scripted-test migration cost.

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Optional, Protocol, Tuple, runtime_checkable

from claim_agent.contracts import ValidationProblem


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
    validation_problems: Tuple[ValidationProblem, ...] = ()
    validated_decision: Optional[Mapping[str, object]] = None
    manual_review_reasons: Tuple[str, ...] = ()

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

        if any(
            not reason.strip()
            for reason in self.manual_review_reasons
        ):
            raise ValueError(
                "manual_review_reasons cannot contain blank values"
            )

        if self.validation_problems:
            structured_messages = tuple(
                problem.message
                for problem in self.validation_problems
            )

            if (
                self.problem_messages
                and self.problem_messages != structured_messages
            ):
                raise ValueError(
                    "problem_messages must match validation_problems"
                )

        if self.validated_decision is not None:
            protected_decision = MappingProxyType(dict(self.validated_decision))
            object.__setattr__(
                self,
                "validated_decision",
                protected_decision,
            )


@runtime_checkable
class ToolDispatcherPort(Protocol):
    """Behavior the runtime requires from a scripted or production dispatcher."""

    def dispatch(
        self,
        request_id: str,
        tool_name: str,
        tool_arguments: Mapping[str, object],
    ) -> OfflineToolObservation:
        """Execute one identified, allowlisted, read-only tool request."""


@runtime_checkable
class DecisionValidatorPort(Protocol):
    """Behavior the runtime requires from a decision validator."""

    def validate(
        self,
        decision_payload: Mapping[str, object],
    ) -> OfflineValidationResult:
        """Validate one proposed decision without model involvement."""
