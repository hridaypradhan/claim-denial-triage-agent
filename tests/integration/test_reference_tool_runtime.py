"""Verify reference-tool authorization through production boundaries."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from typing import Mapping

from claim_agent.agent.integrated_runtime import (
    IntegratedCaseResult,
    run_integrated_case,
)
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from claim_agent.agent.scripted_model import (
    ScriptedModelClient,
    ScriptedModelOutput,
)
from claim_agent.agent.tool_request_guard import ClaimToolContext
from claim_agent.contracts import (
    BaselineFinding,
    Disposition,
    RootCause,
    RouteDecision,
    RouteOutcome,
)
from claim_agent.tools.dispatcher import (
    DENIAL_CODE_TOOL,
    MEDICAL_POLICY_TOOL,
    PAYER_PROFILE_TOOL,
    ToolDispatcher,
)
from claim_agent.tools.knowledge_store import KnowledgeStore

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[2] / "knowledge"

CLAIM_ID = "CLM-2026-005097"
PAYER = "BCBS_AZ"
PROCEDURE_CODE = "70553"
SERVICE_DATE = date(2026, 6, 2)


def make_baseline_finding() -> BaselineFinding:
    """Create an evidence-dependent finding for the active claim line."""

    return BaselineFinding(
        claim_id=CLAIM_ID,
        service_line_id=1,
        denial_codes=["CO-50"],
        root_cause=RootCause.MEDICAL_NECESSITY,
        baseline_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
        required_evidence=["medical_policy", "encounter_note"],
        rationale="Policy and encounter evidence require interpretation.",
        filing_deadline=date(2026, 12, 31),
        deadline_status="ACTIVE",
    )


def make_claim_context(
    *,
    payer: str = PAYER,
    carc: str = "50",
) -> ClaimToolContext:
    """Return trusted claim and denial fields for one reference lookup."""

    return ClaimToolContext(
        claim_id=CLAIM_ID,
        payer=payer,
        procedure_code=PROCEDURE_CODE,
        service_date=SERVICE_DATE,
        denial_carc=carc,
        denial_rarc=(),
        denial_group_code="CO",
    )


def make_route_decision(*authorized_tools: str) -> RouteDecision:
    """Return an agent route with an explicit per-run tool allowlist."""

    return RouteDecision(
        claim_id=CLAIM_ID,
        service_line_id=1,
        outcome=RouteOutcome.AGENT_REQUIRED,
        reason_code="EVIDENCE_EVALUATION_REQUIRED",
        routing_reason="The claim requires evidence interpretation.",
        authorized_tools=list(authorized_tools),
    )


def make_model(
    tool_name: str,
    tool_arguments: Mapping[str, object],
) -> ScriptedModelClient:
    """Return a model that makes exactly one reference-tool request."""

    return ScriptedModelClient(
        [
            ScriptedModelOutput(
                response_text=json.dumps(
                    {
                        "action": "call_tool",
                        "tool_name": tool_name,
                        "tool_arguments": dict(tool_arguments),
                    }
                )
            )
        ]
    )


class ReferenceToolRuntimeTests(unittest.TestCase):
    """Exercise allowlisting, claim binding, and explicit unknown results."""

    def _run_reference_request(
        self,
        *,
        tool_name: str,
        tool_arguments: Mapping[str, object],
        claim_context: ClaimToolContext,
        authorized: bool = True,
    ) -> IntegratedCaseResult:
        """Run one reference request through all production boundaries."""

        authorized_tools = (
            (tool_name,) if authorized else (MEDICAL_POLICY_TOOL,)
        )

        return run_integrated_case(
            run_id=f"reference-runtime-{tool_name}",
            system_instructions="Use only explicitly authorized tools.",
            model_client=make_model(tool_name, tool_arguments),
            dispatcher=ToolDispatcher(KnowledgeStore(KNOWLEDGE_ROOT)),
            baseline_finding=make_baseline_finding(),
            claim_context=claim_context,
            route_decision=make_route_decision(*authorized_tools),
            budget_limits=RuntimeBudgetLimits(
                max_model_calls=1,
                max_tool_calls=1,
                max_repair_attempts=0,
                max_total_steps=2,
                max_runtime_seconds=30.0,
            ),
            as_of=date(2026, 11, 6),
            package_version="test-v1",
            knowledge_snapshot_id="test-knowledge-snapshot",
        )

    @staticmethod
    def _tool_returned_details(
        result: IntegratedCaseResult,
    ) -> Mapping[str, object]:
        """Return the single tool-result trace record from one run."""

        returned_entries = [
            entry
            for entry in result.runtime_result.trace_entries
            if entry.event_name == "TOOL_RETURNED"
        ]
        if len(returned_entries) != 1:
            raise AssertionError(
                "Expected exactly one TOOL_RETURNED trace entry, "
                f"found {len(returned_entries)}."
            )

        return returned_entries[0].details

    def test_authorized_denial_lookup_returns_reference_result(self) -> None:
        """An approved route may inspect its exact active denial code."""

        result = self._run_reference_request(
            tool_name=DENIAL_CODE_TOOL,
            tool_arguments={
                "carc": "50",
                "rarc": [],
                "group_code": "CO",
            },
            claim_context=make_claim_context(),
        )

        returned = self._tool_returned_details(result)
        self.assertTrue(returned["succeeded"])
        self.assertEqual("OK", returned["status"])
        self.assertEqual(["REF-DENIAL-CODES@v1"], returned["source_ids"])

    def test_unapproved_denial_lookup_stops_before_dispatch(self) -> None:
        """A route without reference authority must reject the tool call."""

        result = self._run_reference_request(
            tool_name=DENIAL_CODE_TOOL,
            tool_arguments={
                "carc": "50",
                "rarc": [],
                "group_code": "CO",
            },
            claim_context=make_claim_context(),
            authorized=False,
        )

        self.assertEqual(
            "tool_not_allowed",
            result.runtime_result.final_state.terminal_reason,
        )
        self.assertNotIn(
            "TOOL_REQUESTED",
            [
                entry.event_name
                for entry in result.runtime_result.trace_entries
            ],
        )

    def test_unknown_denial_code_remains_no_match(self) -> None:
        """An unknown active CARC must not receive a guessed description."""

        result = self._run_reference_request(
            tool_name=DENIAL_CODE_TOOL,
            tool_arguments={
                "carc": "999",
                "rarc": [],
                "group_code": "CO",
            },
            claim_context=make_claim_context(carc="999"),
        )

        returned = self._tool_returned_details(result)
        self.assertFalse(returned["succeeded"])
        self.assertEqual("NO_MATCH", returned["error_code"])
        self.assertEqual([], returned["source_ids"])

    def test_unknown_payer_remains_no_match(self) -> None:
        """An unlisted payer must not fall back to a default profile."""

        result = self._run_reference_request(
            tool_name=PAYER_PROFILE_TOOL,
            tool_arguments={"payer_id": "UNKNOWN_PAYER"},
            claim_context=make_claim_context(
                payer="UNKNOWN_PAYER",
                carc="197",
            ),
        )

        returned = self._tool_returned_details(result)
        self.assertFalse(returned["succeeded"])
        self.assertEqual("NO_MATCH", returned["error_code"])
        self.assertEqual([], returned["source_ids"])


if __name__ == "__main__":
    unittest.main()
