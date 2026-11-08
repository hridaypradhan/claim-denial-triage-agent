"""Deterministic model scripts for Dataset A agent-routed cases.

Most Dataset A cases are resolved by the frozen rules. Only two require the
agent, and both must safely request review after retrieval finds no applicable
policy in the current knowledge snapshot.
"""

from __future__ import annotations

import json

from claim_agent.agent.scripted_model import ScriptedModelOutput
from claim_agent.tools.dispatcher import MEDICAL_POLICY_TOOL
from evaluation.scripted_agent import ScriptedCasePlan

SYSTEM_INSTRUCTIONS = (
    "Use only evidence returned by approved tools. Never invent a policy, "
    "citation, or claim fact. Request human review when the available "
    "evidence cannot safely support a decision."
)


def _policy_request(
    payer: str,
    procedure_code: str,
    service_date: str,
    query: str,
) -> ScriptedModelOutput:
    """Build one deterministic request for applicable policy evidence."""

    return ScriptedModelOutput(
        response_text=json.dumps(
            {
                "action": "call_tool",
                "tool_name": MEDICAL_POLICY_TOOL,
                "tool_arguments": {
                    "payer": payer,
                    "procedure_code": procedure_code,
                    "service_date": service_date,
                    "query": query,
                },
            }
        )
    )


def _manual_review(reason: str) -> ScriptedModelOutput:
    """Build the safe terminal action used when retrieval cannot resolve a case."""

    return ScriptedModelOutput(
        response_text=json.dumps(
            {
                "action": "request_manual_review",
                "reason": reason,
            }
        )
    )


DATASET_A_SCRIPTED_PLANS = {
    "CLM-2026-610102-L1": ScriptedCasePlan(
        system_instructions=SYSTEM_INSTRUCTIONS,
        model_outputs=(
            _policy_request(
                payer="BCBS_AZ",
                procedure_code="99213",
                service_date="2026-07-02",
                query="NCCI bundling policy and modifier 59 requirements",
            ),
            _manual_review(
                "No applicable bundling policy is available in the "
                "approved knowledge snapshot."
            ),
        ),
    ),
    "CLM-2026-610110-L1": ScriptedCasePlan(
        system_instructions=SYSTEM_INSTRUCTIONS,
        model_outputs=(
            _policy_request(
                payer="UHC",
                procedure_code="73721",
                service_date="2026-08-01",
                query="prior authorization requirements for MRI",
            ),
            _manual_review(
                "No applicable authorization policy is available in the "
                "approved knowledge snapshot."
            ),
        ),
    ),
}
