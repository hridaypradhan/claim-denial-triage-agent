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
            "UNMAPPED_EVENT",
            {"example": "value"},
        )

        with self.assertRaisesRegex(
            ValueError,
            "does not support internal event UNMAPPED_EVENT",
        ):
            make_adapter().adapt(runtime_trace.entries)

    def test_successful_runtime_history_maps_end_to_end(self) -> None:
        """A complete internal history should become a writable trace."""

        decision = {
            "contract_version": "0.1",
            "decision_id": "decision-1",
            "claim_id": "CLM-2026-005097",
            "service_line_id": 1,
            "root_cause": "MEDICAL_NECESSITY",
            "recommended_disposition": "APPEAL_WITH_DOCUMENTATION",
            "actions": ["Prepare an appeal for human review."],
            "criterion_assessments": [],
            "evidence_gaps": [],
            "agent_status": "COMPLETED",
            "manual_review_reasons": [],
            "rationale": "The cited evidence supports the criteria.",
            "citation_ids": ["POL-1@v1#P1"],
        }
        runtime_trace = InMemoryRuntimeTrace(run_id="run-1")
        runtime_trace.record("RUN_STARTED")
        runtime_trace.record(
            "MODEL_REQUESTED",
            {
                "request_id": "run-1-model-1",
                "prompt_sha256": "a" * 64,
                "repair_attempt": 0,
            },
        )
        runtime_trace.record(
            "MODEL_RESPONDED",
            {
                "request_id": "run-1-model-1",
                "response_text": (
                    '{"action":"call_tool","tool_name":'
                    '"retrieve_medical_policy","tool_arguments":'
                    '{"payer":"BCBSAZ","query":"brain MRI"}}'
                ),
                "input_tokens": None,
                "output_tokens": None,
            },
        )
        runtime_trace.record(
            "TOOL_REQUESTED",
            {
                "request_id": "run-1:tool:1",
                "tool_name": "retrieve_medical_policy",
                "tool_args": {
                    "payer": "BCBSAZ",
                    "query": "brain MRI",
                },
                "tool_call_index": 1,
                "attempt": 1,
            },
        )
        runtime_trace.record(
            "TOOL_RETURNED",
            {
                "request_id": "run-1:tool:1",
                "tool_name": "retrieve_medical_policy",
                "attempt": 1,
                "status": "OK",
                "error_code": "",
                "retryable": False,
                "source_ids": ["POL-1@v1"],
                "passage_ids": ["POL-1@v1#P1"],
            },
        )
        runtime_trace.record(
            "MODEL_REQUESTED",
            {
                "request_id": "run-1-model-2",
                "prompt_sha256": "b" * 64,
                "repair_attempt": 0,
            },
        )
        runtime_trace.record(
            "MODEL_RESPONDED",
            {
                "request_id": "run-1-model-2",
                "response_text": (
                    '{"action":"propose_decision","decision":'
                    + json.dumps(decision)
                    + "}"
                ),
                "input_tokens": None,
                "output_tokens": None,
            },
        )
        runtime_trace.record("DECISION_PROPOSED", {"decision": decision})
        runtime_trace.record(
            "VALIDATION_RETURNED",
            {
                "status": "ACCEPTED",
                "decision_id": "decision-1",
                "claim_id": "CLM-2026-005097",
                "service_line_id": 1,
                "validation_problems": [],
                "validated_decision": decision,
                "manual_review_reasons": [],
            },
        )
        runtime_trace.record(
            "RUN_COMPLETED",
            {"terminal_reason": "decision_accepted"},
        )

        events = make_adapter().adapt(runtime_trace.entries)

        self.assertEqual(events[-1].event_type.value, "RUN_COMPLETED")
        self.assertEqual(
            events[-1].payload["counters"],
            {
                "model_calls": 2,
                "tool_calls": 1,
                "tool_retries": 0,
                "repairs": 0,
                "total_steps": 3,
            },
        )
        self.assertEqual(
            events[5].payload["passage_ids"],
            ["POL-1@v1#P1"],
        )
        self.assertEqual(
            events[2].payload["tool_calls"][0]["tool_args"]["query"],
            "[REDACTED]",
        )
        proposed_event = next(
            event
            for event in events
            if event.event_type.value == "DECISION_PROPOSED"
        )
        self.assertEqual("decision-1", proposed_event.payload["decision_id"])
        self.assertNotIn("decision", proposed_event.payload)

    def test_manual_review_preserves_validator_and_evidence_reasons(self) -> None:
        """Evaluation should retain every coded reason for escalation."""

        runtime_trace = InMemoryRuntimeTrace(run_id="run-1")
        runtime_trace.record("RUN_STARTED")
        runtime_trace.record(
            "TOOL_RETURNED",
            {
                "request_id": "run-1:tool:1",
                "tool_name": "get_encounter_note",
                "attempt": 1,
                "status": "ERROR",
                "error_code": "DOCUMENT_UNAVAILABLE",
                "retryable": False,
                "source_ids": [],
                "passage_ids": [],
            },
        )
        runtime_trace.record(
            "VALIDATION_RETURNED",
            {
                "status": "MANUAL_REVIEW",
                "decision_id": "decision-1",
                "claim_id": "CLM-2026-005097",
                "service_line_id": 1,
                "validation_problems": [],
                "validated_decision": None,
                "manual_review_reasons": [
                    "SAFETY_ESCALATION_MISSING_EVIDENCE"
                ],
            },
        )
        runtime_trace.record(
            "MANUAL_REVIEW",
            {"terminal_reason": "decision_requires_manual_review"},
        )

        terminal_event = make_adapter().adapt(runtime_trace.entries)[-1]

        self.assertEqual(
            [
                "VALIDATOR_REQUIRED_REVIEW",
                "SAFETY_ESCALATION_MISSING_EVIDENCE",
                "MISSING_EVIDENCE",
            ],
            terminal_event.payload["manual_review_reasons"],
        )

    def test_budget_terminal_reasons_use_structured_budget_name(self) -> None:
        """Budget outcomes must not depend on parsing human-readable text."""

        expected_reason_by_budget = {
            "model_call": "MODEL_CALL_BUDGET_EXHAUSTED",
            "tool_call": "TOOL_CALL_BUDGET_EXHAUSTED",
            "repair_attempt": "REPAIR_BUDGET_EXHAUSTED",
            "total_step": "STEP_BUDGET_EXHAUSTED",
        }

        for budget_name, expected_reason in expected_reason_by_budget.items():
            with self.subTest(budget_name=budget_name):
                runtime_trace = InMemoryRuntimeTrace(run_id="run-1")
                runtime_trace.record("RUN_STARTED")
                runtime_trace.record(
                    "MANUAL_REVIEW",
                    {
                        "terminal_reason": "repair_budget_exhausted",
                        "budget_name": budget_name,
                        "problem_message": "wording intentionally irrelevant",
                    },
                )

                terminal_event = make_adapter().adapt(runtime_trace.entries)[-1]

                self.assertEqual(
                    expected_reason,
                    terminal_event.terminal_reason.value,
                )

    def test_completed_run_requires_a_validated_decision(self) -> None:
        """The audit trail must not invent defaults for an impossible success."""

        runtime_trace = InMemoryRuntimeTrace(run_id="run-1")
        runtime_trace.record("RUN_STARTED")
        runtime_trace.record(
            "RUN_COMPLETED",
            {"terminal_reason": "decision_accepted"},
        )

        with self.assertRaisesRegex(
            ValueError,
            "requires a preceding validated decision",
        ):
            make_adapter().adapt(runtime_trace.entries)
