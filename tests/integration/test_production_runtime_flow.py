"""Exercise the offline runtime with production-shaped project boundaries."""

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
from claim_agent.contracts import (
    BaselineFinding,
    Disposition,
    RootCause,
    RouteDecision,
    RouteOutcome,
)
from claim_agent.tools.dispatcher import (
    ENCOUNTER_NOTE_TOOL,
    MEDICAL_POLICY_TOOL,
    ToolDispatcher,
)
from claim_agent.tools.knowledge_store import KnowledgeStore

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[2] / "knowledge"

CLAIM_ID = "CLM-2026-005097"
POLICY_PASSAGE_IDS = (
    "POL-BCBSAZ-MRI-BRAIN@v1#P3",
    "POL-BCBSAZ-MRI-BRAIN@v1#P4",
    "POL-BCBSAZ-MRI-BRAIN@v1#P5",
)
ENCOUNTER_PASSAGE_IDS = (
    "ENC-CLM-2026-005097-PN@v1#L1",
    "ENC-CLM-2026-005097-PN@v1#L2",
    "ENC-CLM-2026-005097-PN@v1#L4",
)


def make_baseline_finding() -> BaselineFinding:
    """Create the deterministic finding that authorizes the agent workflow."""

    return BaselineFinding(
        claim_id=CLAIM_ID,
        service_line_id=1,
        denial_codes=["CO-50"],
        root_cause=RootCause.MEDICAL_NECESSITY,
        baseline_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
        required_evidence=[
            "medical_policy",
            "encounter_note",
        ],
        rationale=("Medical-necessity review requires policy and encounter evidence."),
        filing_deadline=date(2026, 12, 31),
        deadline_status="ACTIVE",
    )


def make_route_decision() -> RouteDecision:
    """Authorize the agent and only the tools needed by this case."""

    return RouteDecision(
        claim_id=CLAIM_ID,
        service_line_id=1,
        outcome=RouteOutcome.AGENT_REQUIRED,
        reason_code="EVIDENCE_EVALUATION_REQUIRED",
        routing_reason=(
            "Medical-necessity review requires policy and encounter evidence."
        ),
        authorized_tools=[
            MEDICAL_POLICY_TOOL,
            ENCOUNTER_NOTE_TOOL,
        ],
    )


def make_scripted_model() -> ScriptedModelClient:
    """Return a deterministic model script for retrieval and decision-making."""

    policy_request = {
        "action": "call_tool",
        "tool_name": MEDICAL_POLICY_TOOL,
        "tool_arguments": {
            "payer": "BCBS_AZ",
            "procedure_code": "70553",
            "service_date": "2026-06-02",
            "query": (
                "headache diplopia neurologic examination " "possible mass contrast"
            ),
        },
    }
    encounter_request = {
        "action": "call_tool",
        "tool_name": ENCOUNTER_NOTE_TOOL,
        "tool_arguments": {
            "claim_id": CLAIM_ID,
            "document_id": "ENC-CLM-2026-005097-PN",
        },
    }
    proposed_decision = {
        "action": "propose_decision",
        "decision": {
            "decision_id": "DEC-INTEGRATED-001",
            "claim_id": CLAIM_ID,
            "service_line_id": 1,
            "root_cause": "MEDICAL_NECESSITY",
            "recommended_disposition": "APPEAL_WITH_DOCUMENTATION",
            "actions": [
                "Prepare a documented appeal for human review.",
            ],
            "criterion_assessments": [
                {
                    "criterion_id": "A",
                    "status": "SUPPORTED",
                    "explanation": ("The record documents headache with diplopia."),
                    "citation_ids": [
                        "ENC-CLM-2026-005097-PN@v1#L1",
                    ],
                },
                {
                    "criterion_id": "B",
                    "status": "SUPPORTED",
                    "explanation": (
                        "The neurologic examination documents a cranial "
                        "nerve finding."
                    ),
                    "citation_ids": [
                        "ENC-CLM-2026-005097-PN@v1#L2",
                    ],
                },
                {
                    "criterion_id": "C",
                    "status": "SUPPORTED",
                    "explanation": (
                        "The consultant documents concern for a possible "
                        "intracranial mass."
                    ),
                    "citation_ids": [
                        "ENC-CLM-2026-005097-PN@v1#L4",
                    ],
                },
            ],
            "evidence_gaps": [],
            "agent_status": "COMPLETED",
            "manual_review_reasons": [],
            "rationale": (
                "The retrieved policy criteria are supported by the " "encounter note."
            ),
            "citation_ids": [
                *POLICY_PASSAGE_IDS,
                *ENCOUNTER_PASSAGE_IDS,
            ],
        },
    }

    return ScriptedModelClient(
        [
            ScriptedModelOutput(
                response_text=json.dumps(policy_request),
            ),
            ScriptedModelOutput(
                response_text=json.dumps(encounter_request),
            ),
            ScriptedModelOutput(
                response_text=json.dumps(proposed_decision),
            ),
        ]
    )


class ProductionRuntimeFlowTests(unittest.TestCase):
    """Verify one evidence-dependent case across production boundaries."""

    def test_real_tools_and_validator_complete_grounded_case(self) -> None:
        """Retrieved same-run evidence should support an accepted decision."""

        shared_dispatcher = ToolDispatcher(KnowledgeStore(KNOWLEDGE_ROOT))
        integrated_result = run_integrated_case(
            run_id="integrated-production-boundaries",
            system_instructions=(
                "Use only retrieved evidence. Return one structured "
                "runtime action per turn. Never submit an appeal."
            ),
            model_client=make_scripted_model(),
            dispatcher=shared_dispatcher,
            baseline_finding=make_baseline_finding(),
            route_decision=make_route_decision(),
            budget_limits=RuntimeBudgetLimits(
                max_model_calls=3,
                max_tool_calls=2,
                max_repair_attempts=1,
                max_total_steps=5,
                max_runtime_seconds=30.0,
            ),
            as_of=date(2026, 11, 6),
        )

        result = integrated_result.runtime_result

        self.assertEqual(
            RuntimePhase.COMPLETED,
            result.final_state.phase,
        )
        self.assertEqual(
            "decision_accepted",
            result.final_state.terminal_reason,
        )
        self.assertEqual(
            "APPEAL_WITH_DOCUMENTATION",
            result.validated_decision["recommended_disposition"],
        )
        self.assertEqual(
            3,
            result.budget_usage.model_calls_used,
        )
        self.assertEqual(
            2,
            result.budget_usage.tool_calls_used,
        )
        self.assertEqual(
            5,
            result.budget_usage.total_steps_used,
        )

        retrieved_passage_ids = {
            passage.passage_id
            for passage in integrated_result.retrieved_evidence
        }

        self.assertTrue(set(POLICY_PASSAGE_IDS).issubset(retrieved_passage_ids))
        self.assertTrue(set(ENCOUNTER_PASSAGE_IDS).issubset(retrieved_passage_ids))
        self.assertEqual(
            "A AND B AND (C OR D)",
              integrated_result.policy_formula,
        )

        trace_event_names = [entry.event_name for entry in result.trace_entries]
        self.assertEqual(
            2,
            trace_event_names.count("TOOL_RETURNED"),
        )
        self.assertIn(
            "VALIDATION_RETURNED",
            trace_event_names,
        )
        self.assertEqual(
            "RUN_COMPLETED",
            trace_event_names[-1],
        )


if __name__ == "__main__":
    unittest.main()
