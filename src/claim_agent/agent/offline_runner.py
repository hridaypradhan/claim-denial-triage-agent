"""Run one deterministic offline demonstration of the agent workflow.

This module uses scripted boundaries so a new contributor can observe the
complete planning, evidence-retrieval, decision, validation, and trace flow
without network access, model credentials, or teammate-owned production code.
"""

from __future__ import annotations

import argparse
import json
from typing import Mapping, Optional, Sequence

from claim_agent.agent.offline_runtime import OfflineAgentRuntime
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from claim_agent.agent.runtime_ports import (
    OfflineToolObservation,
    OfflineValidationResult,
    OfflineValidationStatus,
)
from claim_agent.agent.scripted_model import (
    ScriptedModelClient,
    ScriptedModelOutput,
)
from claim_agent.agent.scripted_ports import (
    ExpectedToolCall,
    ScriptedDecisionValidator,
    ScriptedToolDispatcher,
)

# TODO(#3): Hriday must replace the scripted tool fixture after Silas delivers
# the approved dispatcher, ToolResult, and EvidencePassage contracts.
# TODO(#5): Hriday must replace the free-form task context after Neha delivers
# the approved BaselineFinding and RouteDecision contracts.
# TODO(#6): Hriday must replace the scripted validator after Neha delivers the
# approved AgentDecision and ValidationResult contracts.
# TODO(#7): Hriday must emit the approved durable trace format after Sachin
# defines the required TraceEvent fields and persistence requirements.

DEMO_RUN_ID = "offline-demo-medical-necessity"
POLICY_SOURCE_ID = "POL-BCBSAZ-MRI-BRAIN@v1"
POLICY_TOOL_NAME = "retrieve_medical_policy"


def run_scripted_demo() -> Mapping[str, object]:
    """Run a synthetic claim through every offline runtime boundary.

    The first scripted model response requests policy evidence. After the
    scripted dispatcher returns that evidence, the second response proposes a
    cited appeal. The scripted validator then accepts the decision.

    Returns:
        A JSON-compatible summary suitable for terminal output and integration
        assertions.
    """

    model_client = ScriptedModelClient(
        [
            ScriptedModelOutput(
                response_text="""
                {
                    "action": "call_tool",
                    "tool_name": "retrieve_medical_policy",
                    "tool_arguments": {
                        "payer": "BCBS_AZ",
                        "procedure_code": "70553"
                    }
                }
                """
            ),
            ScriptedModelOutput(
                response_text=f"""
                {{
                    "action": "propose_decision",
                    "decision": {{
                        "recommended_disposition": "APPEAL",
                        "rationale": "The documented criteria support an appeal.",
                        "citation_ids": ["{POLICY_SOURCE_ID}"]
                    }}
                }}
                """
            ),
        ]
    )

    tool_dispatcher = ScriptedToolDispatcher(
        [
            ExpectedToolCall(
                tool_name=POLICY_TOOL_NAME,
                tool_arguments={
                    "payer": "BCBS_AZ",
                    "procedure_code": "70553",
                },
                observation=OfflineToolObservation(
                    succeeded=True,
                    payload={
                        "policy_title": "Brain MRI Medical Necessity",
                        "criteria_formula": "A AND B AND (C OR D)",
                    },
                    source_ids=(POLICY_SOURCE_ID,),
                ),
            )
        ]
    )

    accepted_decision = {
        "recommended_disposition": "APPEAL",
        "rationale": "The documented criteria support an appeal.",
        "citation_ids": [POLICY_SOURCE_ID],
    }
    decision_validator = ScriptedDecisionValidator(
        [
            OfflineValidationResult(
                status=OfflineValidationStatus.ACCEPTED,
                validated_decision=accepted_decision,
            )
        ]
    )

    runtime = OfflineAgentRuntime(
        model_client=model_client,
        tool_dispatcher=tool_dispatcher,
        decision_validator=decision_validator,
        budget_limits=RuntimeBudgetLimits(
            max_model_calls=2,
            max_tool_calls=1,
            max_repair_attempts=1,
            max_total_steps=3,
            max_runtime_seconds=30.0,
        ),
        allowed_tool_names=(POLICY_TOOL_NAME,),
    )

    result = runtime.run(
        run_id=DEMO_RUN_ID,
        system_instructions=(
            "Use only allowlisted tools and cited evidence. "
            "Return a structured runtime action."
        ),
        task_context=(
            "Synthetic claim: BCBS_AZ denied CPT 70553 for medical "
            "necessity. Determine whether retrieved policy evidence "
            "supports an appeal."
        ),
    )

    return {
        "run_id": result.final_state.run_id,
        "phase": result.final_state.phase.value,
        "terminal_reason": result.final_state.terminal_reason,
        "validated_decision": (
            dict(result.validated_decision)
            if result.validated_decision is not None
            else None
        ),
        "budget_usage": {
            "model_calls": result.budget_usage.model_calls_used,
            "tool_calls": result.budget_usage.tool_calls_used,
            "repair_attempts": result.budget_usage.repair_attempts_used,
            "total_steps": result.budget_usage.total_steps_used,
        },
        "trace_events": [
            trace_entry.event_name
            for trace_entry in result.trace_entries
        ],
    }


def main(arguments: Optional[Sequence[str]] = None) -> int:
    """Run the demonstration and print its JSON summary."""

    argument_parser = argparse.ArgumentParser(
        description=(
            "Run the deterministic claim-denial agent demonstration "
            "without network access."
        )
    )
    argument_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Indent the JSON output for easier human reading.",
    )
    parsed_arguments = argument_parser.parse_args(arguments)

    summary = run_scripted_demo()
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
