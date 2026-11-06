"""Run a production-shaped demonstration using the local knowledge corpus.

Unlike the smaller offline demonstration, this runner uses the merged baseline,
routing, tool-dispatch, evidence, and validation contracts. The model remains
scripted so the demonstration is deterministic and requires no credentials.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Mapping, Optional, Sequence

from claim_agent.agent.integrated_runtime import run_integrated_case
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
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

PROJECT_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_ROOT = PROJECT_ROOT / "knowledge"

DEMO_RUN_ID = "integrated-demo-medical-necessity"
DEMO_CLAIM_ID = "CLM-2026-005097"
DEMO_SERVICE_LINE_ID = 1
DEMO_PACKAGE_VERSION = "v1"
DEMO_KNOWLEDGE_SNAPSHOT_ID = "local-corpus-v1"

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


def build_demo_baseline_finding() -> BaselineFinding:
    """Create the deterministic finding that sends this case to the agent."""

    return BaselineFinding(
        claim_id=DEMO_CLAIM_ID,
        service_line_id=DEMO_SERVICE_LINE_ID,
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


def build_demo_route_decision() -> RouteDecision:
    """Authorize only the retrieval tools required by the demo case."""

    return RouteDecision(
        claim_id=DEMO_CLAIM_ID,
        service_line_id=DEMO_SERVICE_LINE_ID,
        outcome=RouteOutcome.AGENT_REQUIRED,
        reason_code="EVIDENCE_EVALUATION_REQUIRED",
        routing_reason=(
            "The agent must compare the medical policy with the encounter note."
        ),
        authorized_tools=[
            MEDICAL_POLICY_TOOL,
            ENCOUNTER_NOTE_TOOL,
        ],
    )


def build_demo_dispatcher() -> ToolDispatcher:
    """Create the production dispatcher over the versioned local corpus."""

    knowledge_store = KnowledgeStore(KNOWLEDGE_ROOT)
    return ToolDispatcher(knowledge_store)


def build_demo_model() -> ScriptedModelClient:
    """Create deterministic model actions for the production-shaped demo."""

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
            "claim_id": DEMO_CLAIM_ID,
            "document_id": "ENC-CLM-2026-005097-PN",
        },
    }
    proposed_decision = {
        "action": "propose_decision",
        "decision": {
            "decision_id": "DEC-INTEGRATED-DEMO-001",
            "claim_id": DEMO_CLAIM_ID,
            "service_line_id": DEMO_SERVICE_LINE_ID,
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
                        "The neurologic examination documents a "
                        "cranial nerve finding."
                    ),
                    "citation_ids": [
                        "ENC-CLM-2026-005097-PN@v1#L2",
                    ],
                },
                {
                    "criterion_id": "C",
                    "status": "SUPPORTED",
                    "explanation": (
                        "The consultant documents concern for a "
                        "possible intracranial mass."
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
                "The retrieved policy criteria are supported by "
                "the encounter note."
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


def run_integrated_demo() -> Mapping[str, object]:
    """Run the routed case through real local tools and validation."""

    integrated_result = run_integrated_case(
        run_id=DEMO_RUN_ID,
        system_instructions=(
            "Use only retrieved evidence. Return one structured runtime "
            "action per turn. Never submit an appeal."
        ),
        model_client=build_demo_model(),
        dispatcher=build_demo_dispatcher(),
        baseline_finding=build_demo_baseline_finding(),
        route_decision=build_demo_route_decision(),
        budget_limits=RuntimeBudgetLimits(
            max_model_calls=3,
            max_tool_calls=2,
            max_repair_attempts=1,
            max_total_steps=5,
            max_runtime_seconds=30.0,
        ),
        as_of=date(2026, 11, 6),
        package_version=DEMO_PACKAGE_VERSION,
        knowledge_snapshot_id=DEMO_KNOWLEDGE_SNAPSHOT_ID,
    )

    runtime_result = integrated_result.runtime_result

    return {
        "run_id": runtime_result.final_state.run_id,
        "phase": runtime_result.final_state.phase.value,
        "terminal_reason": runtime_result.final_state.terminal_reason,
        "validated_decision": (
            dict(runtime_result.validated_decision)
            if runtime_result.validated_decision is not None
            else None
        ),
        "output_package": integrated_result.output_package.model_dump(
            mode="json"
        ),
        "budget_usage": {
            "model_calls": runtime_result.budget_usage.model_calls_used,
            "tool_calls": runtime_result.budget_usage.tool_calls_used,
            "repair_attempts": (
                runtime_result.budget_usage.repair_attempts_used
            ),
            "total_steps": runtime_result.budget_usage.total_steps_used,
        },
        "retrieved_passage_ids": [
            passage.passage_id
            for passage in integrated_result.retrieved_evidence
        ],
        "policy_formula": integrated_result.policy_formula,
        "trace_events": [
            trace_entry.event_name
            for trace_entry in runtime_result.trace_entries
        ],
    }


def main(arguments: Optional[Sequence[str]] = None) -> int:
    """Run the integrated demonstration and print its JSON summary."""

    argument_parser = argparse.ArgumentParser(
        description=(
            "Run the claim-denial agent using the local production-shaped "
            "knowledge and validation boundaries."
        )
    )
    argument_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Indent the JSON output for easier human reading.",
    )
    parsed_arguments = argument_parser.parse_args(arguments)

    summary = run_integrated_demo()
    indentation = 2 if parsed_arguments.pretty else None

    print(
        json.dumps(
            summary,
            indent=indentation,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
