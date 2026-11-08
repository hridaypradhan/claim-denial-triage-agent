"""Tests for the canonical evaluation-facing trace envelope."""

from __future__ import annotations

import unittest

import json
from pathlib import Path

from claim_agent.agent.canonical_trace import (
    TerminalReason,
    TraceComponent,
    TraceEvent,
    TraceEventType,
    UsageStatus,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRACE_EXAMPLE_PATHS = (
    PROJECT_ROOT / "evaluation" / "examples" / "trace_success.jsonl",
    PROJECT_ROOT / "evaluation" / "examples" / "trace_budget_exhausted.jsonl",
    PROJECT_ROOT / "evaluation" / "examples" / "trace_tool_boundary_failure.jsonl",
)


class TraceEventTests(unittest.TestCase):
    """Verify common fields shared by every canonical event."""

    def test_valid_run_started_event_is_accepted(self) -> None:
        """A correctly attributed event should preserve its envelope fields."""

        event = TraceEvent(
            run_id="run-1",
            case_id="claim-1-L1",
            sequence_number=1,
            event_type=TraceEventType.RUN_STARTED,
            component=TraceComponent.AGENT_RUNTIME,
            timestamp="2026-11-07T15:00:00.000Z",
            payload={},
        )

        self.assertEqual(event.trace_version, "0.2")
        self.assertEqual(event.sequence_number, 1)

    def test_event_rejects_zero_sequence_number(self) -> None:
        """Canonical sequence numbers must begin at one."""

        with self.assertRaises(ValueError):
            TraceEvent(
                run_id="run-1",
                case_id="claim-1-L1",
                sequence_number=0,
                event_type=TraceEventType.RUN_STARTED,
                component=TraceComponent.AGENT_RUNTIME,
                timestamp="2026-11-07T15:00:00.000Z",
                payload={},
            )

    def test_event_rejects_wrong_owning_component(self) -> None:
        """A model request must not be attributed to the agent runtime."""

        with self.assertRaisesRegex(
            ValueError,
            "MODEL_REQUESTED must use component MODEL_CLIENT",
        ):
            TraceEvent(
                run_id="run-1",
                case_id="claim-1-L1",
                sequence_number=1,
                event_type=TraceEventType.MODEL_REQUESTED,
                component=TraceComponent.AGENT_RUNTIME,
                timestamp="2026-11-07T15:00:00.000Z",
                payload={},
            )

    def test_unknown_trace_version_is_rejected(self) -> None:
        """Contract changes must not silently masquerade as version 0.2."""

        with self.assertRaises(ValueError):
            TraceEvent(
                trace_version="9.9",
                run_id="run-1",
                case_id="claim-1-L1",
                sequence_number=1,
                event_type=TraceEventType.RUN_STARTED,
                component=TraceComponent.AGENT_RUNTIME,
                timestamp="2026-11-07T15:00:00.000Z",
                payload={},
            )

    def test_model_request_requires_request_id(self) -> None:
        """A model request must be linkable to its eventual response."""

        with self.assertRaisesRegex(
            ValueError,
            "MODEL_REQUESTED requires a non-empty request_id",
        ):
            TraceEvent(
                run_id="run-1",
                case_id="claim-1-L1",
                sequence_number=1,
                event_type=TraceEventType.MODEL_REQUESTED,
                component=TraceComponent.MODEL_CLIENT,
                timestamp="2026-11-07T15:00:00.000Z",
                payload={},
                model_id="example-model-2026-01-01",
                prompt_version="agent-prompt-v0.1",
            )

    def test_non_request_event_rejects_request_id(self) -> None:
        """A run-start event must not pretend to represent a boundary call."""

        with self.assertRaisesRegex(
            ValueError,
            "RUN_STARTED must not include request_id",
        ):
            TraceEvent(
                run_id="run-1",
                case_id="claim-1-L1",
                sequence_number=1,
                event_type=TraceEventType.RUN_STARTED,
                component=TraceComponent.AGENT_RUNTIME,
                timestamp="2026-11-07T15:00:00.000Z",
                payload={},
                request_id="request-1",
            )

    def test_terminal_event_requires_terminal_reason(self) -> None:
        """Evaluation must be able to determine why every run stopped."""

        with self.assertRaisesRegex(
            ValueError,
            "MANUAL_REVIEW requires terminal_reason",
        ):
            TraceEvent(
                run_id="run-1",
                case_id="claim-1-L1",
                sequence_number=2,
                event_type=TraceEventType.MANUAL_REVIEW,
                component=TraceComponent.AGENT_RUNTIME,
                timestamp="2026-11-07T15:00:01.000Z",
                payload={},
                elapsed_ms=1000,
            )

    def test_completed_event_accepts_completed_reason(self) -> None:
        """A validated completion should use its canonical terminal reason."""

        event = TraceEvent(
            run_id="run-1",
            case_id="claim-1-L1",
            sequence_number=2,
            event_type=TraceEventType.RUN_COMPLETED,
            component=TraceComponent.AGENT_RUNTIME,
            timestamp="2026-11-07T15:00:01.000Z",
            elapsed_ms=1000,
            payload={},
            terminal_reason=TerminalReason.COMPLETED_VALIDATED,
        )

        self.assertEqual(
            event.terminal_reason,
            TerminalReason.COMPLETED_VALIDATED,
        )

    def test_model_request_requires_model_identity(self) -> None:
        """A model call must identify the exact model and prompt versions."""

        with self.assertRaisesRegex(
            ValueError,
            "MODEL_REQUESTED requires a non-empty model_id",
        ):
            TraceEvent(
                run_id="run-1",
                case_id="claim-1-L1",
                sequence_number=1,
                event_type=TraceEventType.MODEL_REQUESTED,
                component=TraceComponent.MODEL_CLIENT,
                timestamp="2026-11-07T15:00:00.000Z",
                payload={},
                request_id="request-1",
            )

    def test_reported_model_usage_requires_token_counts(self) -> None:
        """Reported usage is incomplete without both token counts."""

        with self.assertRaisesRegex(
            ValueError,
            "REPORTED usage requires input_tokens and output_tokens",
        ):
            TraceEvent(
                run_id="run-1",
                case_id="claim-1-L1",
                sequence_number=2,
                event_type=TraceEventType.MODEL_RESPONDED,
                component=TraceComponent.MODEL_CLIENT,
                timestamp="2026-11-07T15:00:01.000Z",
                payload={},
                request_id="request-1",
                model_id="example-model-2026-01-01",
                elapsed_ms=500,
                usage_status=UsageStatus.REPORTED,
            )

    def test_scripted_model_usage_accepts_unavailable_tokens(self) -> None:
        """The offline fake model has no provider token accounting."""

        event = TraceEvent(
            run_id="run-1",
            case_id="claim-1-L1",
            sequence_number=2,
            event_type=TraceEventType.MODEL_RESPONDED,
            component=TraceComponent.MODEL_CLIENT,
            timestamp="2026-11-07T15:00:01.000Z",
            payload={},
            request_id="request-1",
            model_id="scripted-fake-model",
            elapsed_ms=1,
            usage_status=UsageStatus.NOT_APPLICABLE,
        )

        self.assertIsNone(event.input_tokens)
        self.assertIsNone(event.output_tokens)

    def test_terminal_event_requires_elapsed_time(self) -> None:
        """Every completed run must report its total observed duration."""

        with self.assertRaisesRegex(
            ValueError,
            "RUN_COMPLETED requires elapsed_ms",
        ):
            TraceEvent(
                run_id="run-1",
                case_id="claim-1-L1",
                sequence_number=2,
                event_type=TraceEventType.RUN_COMPLETED,
                component=TraceComponent.AGENT_RUNTIME,
                timestamp="2026-11-07T15:00:01.000Z",
                payload={},
                terminal_reason=TerminalReason.COMPLETED_VALIDATED,
            )


class AcceptedTraceExampleTests(unittest.TestCase):
    """Verify compatibility with the examples accepted through PR #8."""

    def load_trace_events(self, trace_path: Path) -> list[TraceEvent]:
        """Parse every JSON Lines record into the canonical event model."""

        return [
            TraceEvent.model_validate(json.loads(line))
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_every_example_event_matches_the_contract(self) -> None:
        """Every committed example line must be a valid canonical event."""

        for trace_path in TRACE_EXAMPLE_PATHS:
            with self.subTest(trace=trace_path.name):
                events = self.load_trace_events(trace_path)

                self.assertGreater(len(events), 0)

    def test_example_sequences_are_contiguous(self) -> None:
        """Every run must use one-based sequence numbers without gaps."""

        for trace_path in TRACE_EXAMPLE_PATHS:
            with self.subTest(trace=trace_path.name):
                events = self.load_trace_events(trace_path)

                self.assertEqual(
                    [event.sequence_number for event in events],
                    list(range(1, len(events) + 1)),
                )

    def test_examples_have_one_terminal_final_event(self) -> None:
        """Each example must end with exactly one canonical terminal event."""

        terminal_types = {
            TraceEventType.RUN_COMPLETED,
            TraceEventType.MANUAL_REVIEW,
        }

        for trace_path in TRACE_EXAMPLE_PATHS:
            with self.subTest(trace=trace_path.name):
                events = self.load_trace_events(trace_path)
                terminal_events = [
                    event for event in events if event.event_type in terminal_types
                ]

                self.assertEqual(events[0].event_type, TraceEventType.RUN_STARTED)
                self.assertEqual(len(terminal_events), 1)
                self.assertIs(events[-1], terminal_events[0])
