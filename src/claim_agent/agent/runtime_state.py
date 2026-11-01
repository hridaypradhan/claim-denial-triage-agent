"""Runtime phases and controlled transitions for one agent run.

The runtime state machine prevents components from skipping required stages.
For example, a proposed decision must pass through validation before the run can
be marked complete.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Dict, FrozenSet, Optional


class RuntimePhase(str, Enum):
    """Named stages through which an evidence-dependent case may move."""

    INITIALIZING = "INITIALIZING"
    PLANNING = "PLANNING"
    WAITING_FOR_TOOL = "WAITING_FOR_TOOL"
    UPDATING_EVIDENCE = "UPDATING_EVIDENCE"
    PROPOSING_DECISION = "PROPOSING_DECISION"
    VALIDATING = "VALIDATING"
    REPAIRING = "REPAIRING"
    COMPLETED = "COMPLETED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


TERMINAL_PHASES: FrozenSet[RuntimePhase] = frozenset(
    {
        RuntimePhase.COMPLETED,
        RuntimePhase.MANUAL_REVIEW,
    }
)


# This table is the runtime's traffic map. Each key is the current phase, and
# its value contains the only phases that may legally follow it.
_ALLOWED_TRANSITIONS: Dict[RuntimePhase, FrozenSet[RuntimePhase]] = {
    RuntimePhase.INITIALIZING: frozenset(
        {
            RuntimePhase.PLANNING,
            RuntimePhase.MANUAL_REVIEW,
        }
    ),
    RuntimePhase.PLANNING: frozenset(
        {
            RuntimePhase.WAITING_FOR_TOOL,
            RuntimePhase.PROPOSING_DECISION,
            RuntimePhase.REPAIRING,
            RuntimePhase.MANUAL_REVIEW,
        }
    ),
    RuntimePhase.WAITING_FOR_TOOL: frozenset(
        {
            RuntimePhase.UPDATING_EVIDENCE,
            RuntimePhase.MANUAL_REVIEW,
        }
    ),
    RuntimePhase.UPDATING_EVIDENCE: frozenset(
        {
            RuntimePhase.PLANNING,
            RuntimePhase.MANUAL_REVIEW,
        }
    ),
    RuntimePhase.PROPOSING_DECISION: frozenset(
        {
            RuntimePhase.VALIDATING,
            RuntimePhase.REPAIRING,
            RuntimePhase.MANUAL_REVIEW,
        }
    ),
    RuntimePhase.VALIDATING: frozenset(
        {
            RuntimePhase.COMPLETED,
            RuntimePhase.REPAIRING,
            RuntimePhase.MANUAL_REVIEW,
        }
    ),
    RuntimePhase.REPAIRING: frozenset(
        {
            RuntimePhase.PLANNING,
            RuntimePhase.PROPOSING_DECISION,
            RuntimePhase.MANUAL_REVIEW,
        }
    ),
    RuntimePhase.COMPLETED: frozenset(),
    RuntimePhase.MANUAL_REVIEW: frozenset(),
}


class RuntimeTransitionError(ValueError):
    """Raised when code attempts an illegal runtime phase change."""


@dataclass(frozen=True)
class AgentRuntimeState:
    """Current lifecycle state for one agent run.

    The object is immutable. Calling transition_to creates a new state instead
    of modifying the existing state. This makes the sequence of runtime changes
    easier to reason about and later record in an append-only trace.

    Attributes:
        run_id: Stable identifier shared by every event in this run.
        phase: Current lifecycle phase.
        terminal_reason: Explanation of why the run stopped. This is required
            only when the phase is COMPLETED or MANUAL_REVIEW.
    """

    run_id: str
    phase: RuntimePhase = RuntimePhase.INITIALIZING
    terminal_reason: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate identifiers and terminal-state information."""

        if not self.run_id.strip():
            raise ValueError("run_id must contain a non-whitespace value")

        is_terminal = self.phase in TERMINAL_PHASES
        has_terminal_reason = bool(
            self.terminal_reason and self.terminal_reason.strip()
        )

        if is_terminal and not has_terminal_reason:
            raise ValueError("terminal_reason is required for a terminal runtime phase")

        if not is_terminal and self.terminal_reason is not None:
            raise ValueError(
                "terminal_reason can only be set for a terminal runtime phase"
            )

    @property
    def is_terminal(self) -> bool:
        """Return whether the run has finished and cannot transition again."""

        return self.phase in TERMINAL_PHASES

    def transition_to(
        self,
        next_phase: RuntimePhase,
        terminal_reason: Optional[str] = None,
    ) -> "AgentRuntimeState":
        """Create the next state after checking that the transition is legal.

        Args:
            next_phase: Phase the runtime wants to enter.
            terminal_reason: Required explanation when entering a terminal
                phase. It must be omitted for nonterminal phases.

        Returns:
            A new immutable AgentRuntimeState in the requested phase.

        Raises:
            RuntimeTransitionError: If the requested phase cannot legally
                follow the current phase.
            ValueError: If terminal-reason information is inconsistent with the
                requested phase.
        """

        allowed_next_phases = _ALLOWED_TRANSITIONS[self.phase]

        if next_phase not in allowed_next_phases:
            raise RuntimeTransitionError(
                f"Cannot transition from {self.phase.value} " f"to {next_phase.value}."
            )

        return replace(
            self,
            phase=next_phase,
            terminal_reason=terminal_reason,
        )
