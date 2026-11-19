"""The runtime consumes calculate_deadline safely."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from claim_agent.agent.integrated_runtime import run_integrated_case
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from claim_agent.agent.runtime_state import RuntimePhase
from claim_agent.agent.scripted_model import (
    ScriptedModelClient,
    ScriptedModelOutput,
)
from claim_agent.contracts import Disposition, ValidationOutcome
from claim_agent.tools.deadline_tool import DEADLINE_TOOL
from claim_agent.tools.dispatcher import (
    ENCOUNTER_NOTE_TOOL,
    MEDICAL_POLICY_TOOL,
    ToolDispatcher,
)
from claim_agent.tools.knowledge_store import KnowledgeStore

from tests.integration.test_claim_bound_runtime import (
    CLAIM_ID,
    make_baseline_finding,
    make_claim_context,
    make_route_decision,
)

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[2] / "knowledge"
AS_OF = date(2026, 11, 6)
OWN_LINE = {"claim_id": CLAIM_ID, "service_line_id": 1}


def output(action):
    return ScriptedModelOutput(response_text=json.dumps(action))


def call_deadline(arguments=OWN_LINE):
    return output(
        {
            "action": "call_tool",
            "tool_name": DEADLINE_TOOL,
            "tool_arguments": dict(arguments),
        }
    )


def appeal_decision():
    return output(
        {
            "action": "propose_decision",
            "decision": {
                "decision_id": "D-1",
                "claim_id": CLAIM_ID,
                "service_line_id": 1,
                "root_cause": "MEDICAL_NECESSITY",
                "recommended_disposition": "APPEAL_WITH_DOCUMENTATION",
                "actions": ["Appeal."],
                "criterion_assessments": [],
                "evidence_gaps": [],
                "agent_status": "COMPLETED",
                "manual_review_reasons": [],
                "rationale": "The deadline tool was consulted.",
                "citation_ids": [],
            },
        }
    )


def run(outputs, authorize=True, finding=None):
    route = make_route_decision()
    if authorize:
        route = route.model_copy(
            update={
                "authorized_tools": [
                    MEDICAL_POLICY_TOOL,
                    ENCOUNTER_NOTE_TOOL,
                    DEADLINE_TOOL,
                ]
            }
        )
    return run_integrated_case(
        run_id="deadline-tool-test",
        system_instructions="Use only the run's own deadline.",
        model_client=ScriptedModelClient(outputs),
        dispatcher=ToolDispatcher(KnowledgeStore(KNOWLEDGE_ROOT)),
        baseline_finding=finding or make_baseline_finding(),
        claim_context=make_claim_context(),
        route_decision=route,
        budget_limits=RuntimeBudgetLimits(
            max_model_calls=3,
            max_tool_calls=2,
            max_repair_attempts=1,
            max_total_steps=6,
            max_runtime_seconds=30.0,
        ),
        as_of=AS_OF,
        package_version="test-v1",
        knowledge_snapshot_id="test-knowledge-snapshot",
    )


def events(result):
    return [entry.event_name for entry in result.runtime_result.trace_entries]


class DeadlineToolRuntimeTest(unittest.TestCase):
    def test_authorized_call_is_traced_and_adds_no_evidence(self):
        result = run(
            [
                call_deadline(),
                output(
                    {
                        "action": "request_manual_review",
                        "reason": "done",
                    }
                ),
            ]
        )

        self.assertIn("TOOL_RETURNED", events(result))
        self.assertNotIn("TOOL_FAILED", events(result))
        self.assertEqual(result.runtime_result.budget_usage.tool_calls_used, 1)
        self.assertEqual(result.retrieved_evidence, ())

    def test_unauthorized_route_never_dispatches(self):
        result = run([call_deadline()], authorize=False)

        self.assertEqual(
            result.runtime_result.final_state.terminal_reason,
            "tool_not_allowed",
        )
        self.assertEqual(result.runtime_result.budget_usage.tool_calls_used, 0)

    def test_another_claim_stops_for_manual_review(self):
        result = run(
            [
                call_deadline(
                    {
                        "claim_id": "CLM-2026-999999",
                        "service_line_id": 1,
                    }
                )
            ]
        )

        self.assertEqual(
            result.runtime_result.final_state.phase,
            RuntimePhase.MANUAL_REVIEW,
        )
        self.assertEqual(
            result.runtime_result.final_state.terminal_reason,
            "tool_boundary_error",
        )
        self.assertIn("TOOL_FAILED", events(result))

    def test_the_tool_cannot_get_an_appeal_past_an_expired_deadline(self):
        expired = make_baseline_finding().model_copy(
            update={
                "filing_deadline": date(2026, 7, 8),
                "deadline_status": "EXPIRED",
            }
        )
        result = run(
            [call_deadline(), appeal_decision(), appeal_decision()],
            finding=expired,
        )

        self.assertEqual(
            result.output_package.final_disposition,
            Disposition.MANUAL_REVIEW,
        )
        self.assertIsNot(result.validation_result.outcome, ValidationOutcome.ACCEPTED)
        self.assertIn(
            "DEADLINE_EXPIRED",
            [problem.code for problem in result.validation_result.validation_problems],
        )


if __name__ == "__main__":
    unittest.main()
