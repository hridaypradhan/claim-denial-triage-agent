"""Deterministic fixtures and configuration for the integrated CLI demo."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Tuple

from baseline.triage.claim import load_claim

from claim_agent.agent.run_configuration import (
    AgentRunConfig,
    AllowedTool,
    ModelParameters,
)
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from claim_agent.agent.scripted_model import (
    ScriptedModelClient,
    ScriptedModelOutput,
)
from claim_agent.agent.tool_request_guard import (
    ClaimToolContext,
    build_claim_tool_context,
)
from claim_agent.agent.router import analyze_and_route_claim
from claim_agent.contracts import (
    BaselineFinding,
    RouteDecision,
)
from claim_agent.tools.dispatcher import (
    ENCOUNTER_NOTE_TOOL,
    MEDICAL_POLICY_TOOL,
    ToolDispatcher,
)
from claim_agent.tools.knowledge_store import KnowledgeStore

PROJECT_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_ROOT = PROJECT_ROOT / "knowledge"
DEMO_CLAIM_PATH = (
    PROJECT_ROOT / "baseline" / "examples" / "test2_medical_necessity.json"
)

DEMO_RUN_ID = "integrated-demo-medical-necessity"
DEMO_CLAIM_ID = "CLM-2026-005097"
DEMO_SERVICE_LINE_ID = 1
DEMO_PACKAGE_VERSION = "v1"
DEMO_CODE_COMMIT = "working-tree"
DEMO_AS_OF = date(2026, 11, 6)

DEMO_BUDGET_LIMITS = RuntimeBudgetLimits(
    max_model_calls=3,
    max_tool_calls=2,
    max_repair_attempts=1,
    max_total_steps=5,
    max_runtime_seconds=30.0,
)

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


def build_demo_routed_case() -> Tuple[BaselineFinding, RouteDecision]:
    """Run the baseline and router on the demo claim's service line.

    The finding's deadline and the route come from the same deterministic
    code a real run uses, so the demo never shows a hand-written deadline.
    """

    claim = load_claim(DEMO_CLAIM_PATH)
    for finding, route in analyze_and_route_claim(claim, DEMO_AS_OF):
        if finding.service_line_id == DEMO_SERVICE_LINE_ID:
            return finding, route
    raise ValueError(
        f"Demo claim has no finding for service line {DEMO_SERVICE_LINE_ID}."
    )


def build_demo_baseline_finding() -> BaselineFinding:
    """Return the router's finding that sends this case to the agent."""

    return build_demo_routed_case()[0]


def build_demo_route_decision() -> RouteDecision:
    """Return the router's decision, which authorizes the retrieval tools."""

    return build_demo_routed_case()[1]


def build_demo_claim_context() -> ClaimToolContext:
    """Build trusted retrieval fields from the demonstration claim."""

    claim = load_claim(DEMO_CLAIM_PATH)
    return build_claim_tool_context(
        claim=claim,
        service_line_id=DEMO_SERVICE_LINE_ID,
    )


def build_demo_dispatcher(knowledge_store: KnowledgeStore) -> ToolDispatcher:
    """Create the production dispatcher over the supplied knowledge snapshot."""

    return ToolDispatcher(knowledge_store)


def build_demo_run_configuration(
    knowledge_snapshot_id: str,
    code_commit: str = DEMO_CODE_COMMIT,
) -> AgentRunConfig:
    """Describe every frozen setting needed to reproduce the demo run."""

    allowed_tools = tuple(
        AllowedTool(tool_name=tool_name, tool_version="0.1")
        for tool_name in sorted({MEDICAL_POLICY_TOOL, ENCOUNTER_NOTE_TOOL})
    )

    return AgentRunConfig(
        config_version="0.2",
        code_commit=code_commit,
        model_provider="scripted",
        model_id="scripted-model-v1",
        model_parameters=ModelParameters(
            max_output_tokens=4096,
            temperature=0.0,
            top_p=None,
        ),
        prompt_version="integrated-demo-v1",
        runtime_seed=0,
        provider_seed=None,
        provider_seed_supported=False,
        max_model_calls=DEMO_BUDGET_LIMITS.max_model_calls,
        max_tool_calls=DEMO_BUDGET_LIMITS.max_tool_calls,
        max_repair_attempts=DEMO_BUDGET_LIMITS.max_repair_attempts,
        max_total_steps=DEMO_BUDGET_LIMITS.max_total_steps,
        max_tool_retries=1,
        max_runtime_seconds=DEMO_BUDGET_LIMITS.max_runtime_seconds,
        model_request_timeout_seconds=30.0,
        tool_request_timeout_seconds=30.0,
        allowed_tools=allowed_tools,
        knowledge_snapshot_id=knowledge_snapshot_id,
    )


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
                "headache diplopia neurologic examination possible mass contrast"
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
            "actions": ["Prepare a documented appeal for human review."],
            "criterion_assessments": [
                {
                    "criterion_id": "A",
                    "status": "SUPPORTED",
                    "explanation": "The record documents headache with diplopia.",
                    "citation_ids": [ENCOUNTER_PASSAGE_IDS[0]],
                },
                {
                    "criterion_id": "B",
                    "status": "SUPPORTED",
                    "explanation": (
                        "The neurologic examination documents a cranial nerve finding."
                    ),
                    "citation_ids": [ENCOUNTER_PASSAGE_IDS[1]],
                },
                {
                    "criterion_id": "C",
                    "status": "SUPPORTED",
                    "explanation": (
                        "The consultant documents concern for a possible "
                        "intracranial mass."
                    ),
                    "citation_ids": [ENCOUNTER_PASSAGE_IDS[2]],
                },
            ],
            "evidence_gaps": [],
            "agent_status": "COMPLETED",
            "manual_review_reasons": [],
            "rationale": (
                "The retrieved policy criteria are supported by the encounter note."
            ),
            "citation_ids": [*POLICY_PASSAGE_IDS, *ENCOUNTER_PASSAGE_IDS],
        },
    }

    return ScriptedModelClient(
        [
            ScriptedModelOutput(response_text=json.dumps(policy_request)),
            ScriptedModelOutput(response_text=json.dumps(encounter_request)),
            ScriptedModelOutput(response_text=json.dumps(proposed_decision)),
        ]
    )
