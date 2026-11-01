"""Deterministic stand-ins for teammate-owned runtime boundaries.

The scripted dispatcher and validator let the offline vertical slice exercise
real runtime control flow before the production tool and validation contracts
are available.
"""

from __future__ import annotations

# TODO(#3): Replace ScriptedToolDispatcher with Silas's contract-backed
# dispatcher adapter when the approved tool contracts are available.
# TODO(#6): Replace ScriptedDecisionValidator with Neha's contract-backed
# validator adapter when the approved validation contracts are available.

from collections import deque
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping, Tuple

from claim_agent.agent.runtime_ports import (
    OfflineToolObservation,
    OfflineValidationResult,
)


class ScriptedBoundaryExhaustedError(RuntimeError):
    """Raised when the runtime makes more calls than a script defines."""


class ScriptedToolCallMismatchError(AssertionError):
    """Raised when the runtime makes a different tool call than expected."""


@dataclass(frozen=True)
class ExpectedToolCall:
    """One expected dispatcher call and the observation it should return."""

    tool_name: str
    tool_arguments: Mapping[str, object]
    observation: OfflineToolObservation

    def __post_init__(self) -> None:
        """Validate and protect the expected tool request."""

        if not self.tool_name.strip():
            raise ValueError("tool_name must contain a non-whitespace value")

        object.__setattr__(
            self,
            "tool_arguments",
            MappingProxyType(dict(self.tool_arguments)),
        )


@dataclass(frozen=True)
class RecordedToolCall:
    """One tool request actually received from the runtime."""

    tool_name: str
    tool_arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        """Protect the recorded arguments from later mutation."""

        object.__setattr__(
            self,
            "tool_arguments",
            MappingProxyType(dict(self.tool_arguments)),
        )


class ScriptedToolDispatcher:
    """Verify and answer tool calls in a predefined order."""

    def __init__(
        self,
        expected_calls: Iterable[ExpectedToolCall],
    ) -> None:
        """Store an independent queue of expected tool calls."""

        self._remaining_calls = deque(expected_calls)
        self._recorded_calls: list[RecordedToolCall] = []

    @property
    def recorded_calls(self) -> Tuple[RecordedToolCall, ...]:
        """Return every tool call successfully matched so far."""

        return tuple(self._recorded_calls)

    @property
    def remaining_call_count(self) -> int:
        """Return how many expected calls have not yet occurred."""

        return len(self._remaining_calls)

    def dispatch(
        self,
        tool_name: str,
        tool_arguments: Mapping[str, object],
    ) -> OfflineToolObservation:
        """Verify one call against the script and return its observation.

        A mismatched call is not removed from the script. This preserves the
        expected call for inspection after a failed test.
        """

        if not self._remaining_calls:
            raise ScriptedBoundaryExhaustedError(
                "The scripted dispatcher has no expected call remaining."
            )

        expected_call = self._remaining_calls[0]

        if tool_name != expected_call.tool_name:
            raise ScriptedToolCallMismatchError(
                f"Expected tool {expected_call.tool_name!r}, "
                f"but received {tool_name!r}."
            )

        received_arguments = dict(tool_arguments)
        expected_arguments = dict(expected_call.tool_arguments)

        if received_arguments != expected_arguments:
            raise ScriptedToolCallMismatchError(
                f"Expected arguments {expected_arguments!r}, "
                f"but received {received_arguments!r}."
            )

        self._remaining_calls.popleft()
        self._recorded_calls.append(
            RecordedToolCall(
                tool_name=tool_name,
                tool_arguments=received_arguments,
            )
        )

        return expected_call.observation


class ScriptedDecisionValidator:
    """Return predefined validation results in first-in, first-out order."""

    def __init__(
        self,
        scripted_results: Iterable[OfflineValidationResult],
    ) -> None:
        """Store validation results and an empty decision history."""

        self._remaining_results = deque(scripted_results)
        self._received_decisions: list[Mapping[str, object]] = []

    @property
    def received_decisions(self) -> Tuple[Mapping[str, object], ...]:
        """Return immutable copies of decisions received so far."""

        return tuple(self._received_decisions)

    @property
    def remaining_result_count(self) -> int:
        """Return how many scripted validation results remain."""

        return len(self._remaining_results)

    def validate(
        self,
        decision_payload: Mapping[str, object],
    ) -> OfflineValidationResult:
        """Record a proposed decision and return the next scripted result."""

        if not self._remaining_results:
            raise ScriptedBoundaryExhaustedError(
                "The scripted validator has no result remaining."
            )

        protected_decision = MappingProxyType(dict(decision_payload))
        self._received_decisions.append(protected_decision)

        return self._remaining_results.popleft()
