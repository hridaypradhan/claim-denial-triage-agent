"""Tests for translation from internal to canonical runtime traces."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from claim_agent.agent.run_configuration import AgentRunConfig
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace
from claim_agent.agent.trace_adapter import CanonicalTraceAdapter
from claim_agent.contracts import RouteDecision, RouteOutcome

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIGURATION_VECTOR_PATH = (
    PROJECT_ROOT / "evaluation" / "examples" / "config_hash_vector.json"
)
FIXED_TIMESTAMP = "2026-11-07T15:00:00.000Z"


def make_adapter() -> CanonicalTraceAdapter:
    """Build an adapter using the accepted configuration example."""

    configuration_vector = json.loads(
        CONFIGURATION_VECTOR_PATH.read_text(encoding="utf-8")
    )
    run_configuration = AgentRunConfig.model_validate(configuration_vector["config"])
    route_decision = RouteDecision(
        claim_id="CLM-2026-005097",
        service_line_id=1,
        outcome=RouteOutcome.AGENT_REQUIRED,
        reason_code="EVIDENCE_EVALUATION_REQUIRED",
        routing_reason="The agent must evaluate retrieved evidence.",
        authorized_tools=[
            "retrieve_medical_policy",
            "get_encounter_note",
        ],
    )

    return CanonicalTraceAdapter(
        run_configuration=run_configuration,
        route_decision=route_decision,
        timestamp_provider=lambda: FIXED_TIMESTAMP,
        evaluation_context={
            "experiment_id": "EXP-DEVELOPMENT",
            "repeat_index": 1,
        },
    )


class CanonicalTraceAdapterTests(unittest.TestCase):
    """Verify initial runtime-to-contract mappings."""

    def test_run_started_contains_reproducibility_metadata(self) -> None:
        """The first event should freeze configuration and route information."""

        runtime_trace = InMemoryRuntimeTrace(run_id="run-1")
        runtime_trace.record(
            "RUN_STARTED",
            {"phase": "INITIALIZING"},
        )

        events = make_adapter().adapt(runtime_trace.entries)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type.value, "RUN_STARTED")
        self.assertEqual(events[0].sequence_number, 1)
        self.assertEqual(
            events[0].case_id,
            "CLM-2026-005097-L1",
        )
        self.assertEqual(
            events[0].payload["config_sha256"],
            "94ec85048641b5ffdff51ea7c265c296c7ae4433a5aa3fa5459c4e08c03ffc23",
        )
        self.assertEqual(
            events[0].payload["route"]["reason_code"],
            "EVIDENCE_EVALUATION_REQUIRED",
        )
        self.assertNotIn(
            "routing_reason",
            events[0].payload["route"],
        )

    def test_phase_changes_are_omitted(self) -> None:
        """Internal state transitions should not duplicate observable events."""

        runtime_trace = InMemoryRuntimeTrace(run_id="run-1")
        runtime_trace.record("RUN_STARTED")
        runtime_trace.record(
            "PHASE_CHANGED",
            {"phase": "PLANNING"},
        )

        events = make_adapter().adapt(runtime_trace.entries)

        self.assertEqual(
            [event.event_type.value for event in events],
            ["RUN_STARTED"],
        )

    def test_unsupported_event_fails_clearly(self) -> None:
        """An unmapped event must not silently disappear from audit history."""

        runtime_trace = InMemoryRuntimeTrace(run_id="run-1")
        runtime_trace.record(
            "MODEL_REQUESTED",
            {"request_id": "run-1:model:1"},
        )

        with self.assertRaisesRegex(
            ValueError,
            "does not yet support internal event MODEL_REQUESTED",
        ):
            make_adapter().adapt(runtime_trace.entries)
