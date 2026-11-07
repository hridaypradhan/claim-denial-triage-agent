"""Tests for controlled agent-runtime phase transitions."""

from __future__ import annotations

import unittest

from claim_agent.agent.runtime_state import (
    AgentRuntimeState,
    RuntimePhase,
    RuntimeTransitionError,
)


class AgentRuntimeStateTests(unittest.TestCase):
    """Verify legal, illegal, and terminal runtime behavior."""

    def test_new_runtime_starts_in_initializing_phase(self) -> None:
        """Every case should begin before planning or tool execution."""

        state = AgentRuntimeState(run_id="run-1")

        self.assertEqual(state.phase, RuntimePhase.INITIALIZING)
        self.assertFalse(state.is_terminal)
        self.assertIsNone(state.terminal_reason)

    def test_legal_transition_creates_state_in_next_phase(self) -> None:
        """Initialization may legally proceed to planning."""

        initial_state = AgentRuntimeState(run_id="run-1")

        planning_state = initial_state.transition_to(RuntimePhase.PLANNING)

        self.assertEqual(planning_state.phase, RuntimePhase.PLANNING)
        self.assertEqual(planning_state.run_id, initial_state.run_id)

    def test_transition_does_not_modify_previous_state(self) -> None:
        """Historical state objects should remain unchanged for traceability."""

        initial_state = AgentRuntimeState(run_id="run-1")

        planning_state = initial_state.transition_to(RuntimePhase.PLANNING)

        self.assertEqual(initial_state.phase, RuntimePhase.INITIALIZING)
        self.assertEqual(planning_state.phase, RuntimePhase.PLANNING)

    def test_illegal_transition_is_rejected(self) -> None:
        """A run cannot skip planning and validation to become complete."""

        initial_state = AgentRuntimeState(run_id="run-1")

        with self.assertRaisesRegex(
            RuntimeTransitionError,
            "INITIALIZING.*COMPLETED",
        ):
            initial_state.transition_to(
                RuntimePhase.COMPLETED,
                terminal_reason="decision_accepted",
            )

    def test_terminal_phase_requires_reason(self) -> None:
        """A stopped run must explain why it stopped."""

        validating_state = AgentRuntimeState(
            run_id="run-1",
            phase=RuntimePhase.VALIDATING,
        )

        with self.assertRaisesRegex(ValueError, "terminal_reason"):
            validating_state.transition_to(RuntimePhase.COMPLETED)

    def test_nonterminal_phase_rejects_terminal_reason(self) -> None:
        """A reason for stopping cannot be attached while work continues."""

        initial_state = AgentRuntimeState(run_id="run-1")

        with self.assertRaisesRegex(ValueError, "terminal_reason"):
            initial_state.transition_to(
                RuntimePhase.PLANNING,
                terminal_reason="should_not_be_present",
            )

    def test_completed_run_cannot_transition_again(self) -> None:
        """No model or tool work may occur after successful completion."""

        completed_state = AgentRuntimeState(
            run_id="run-1",
            phase=RuntimePhase.COMPLETED,
            terminal_reason="decision_accepted",
        )

        with self.assertRaisesRegex(
            RuntimeTransitionError,
            "COMPLETED.*PLANNING",
        ):
            completed_state.transition_to(RuntimePhase.PLANNING)

    def test_planning_can_end_in_manual_review(self) -> None:
        """An unresolved or unsafe case may stop conservatively."""

        planning_state = AgentRuntimeState(
            run_id="run-1",
            phase=RuntimePhase.PLANNING,
        )

        manual_review_state = planning_state.transition_to(
            RuntimePhase.MANUAL_REVIEW,
            terminal_reason="required_evidence_unavailable",
        )

        self.assertTrue(manual_review_state.is_terminal)
        self.assertEqual(
            manual_review_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            manual_review_state.terminal_reason,
            "required_evidence_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
