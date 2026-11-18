"""Verify eligibility retrieval through the integrated agent runtime."""

from __future__ import annotations

import json
import shutil
import tempfile
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
from claim_agent.agent.tool_request_guard import ClaimToolContext
from claim_agent.contracts import (
    BaselineFinding,
    Disposition,
    RootCause,
    RouteDecision,
    RouteOutcome,
)
from claim_agent.tools.dispatcher import ToolDispatcher
from claim_agent.tools.eligibility import ELIGIBILITY_ROOT, ELIGIBILITY_TOOL
from claim_agent.tools.knowledge_store import KnowledgeStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_ROOT = REPOSITORY_ROOT / "knowledge"
CLAIM_ID = "CLM-2026-610105"
PATIENT_ACCOUNT = "PA-SYN-10105"
AS_OF = date(2026, 9, 6)


def model_output(action: dict[str, object]) -> ScriptedModelOutput:
    """Encode one scripted model action."""

    return ScriptedModelOutput(response_text=json.dumps(action))


def call_eligibility(
    *,
    patient_account: str = PATIENT_ACCOUNT,
    payer: str = "UHC",
) -> ScriptedModelOutput:
    """Request eligibility evidence for one member and payer."""

    return model_output(
        {
            "action": "call_tool",
            "tool_name": ELIGIBILITY_TOOL,
            "tool_arguments": {
                "patient_account": patient_account,
                "payer": payer,
            },
        }
    )


def request_manual_review() -> ScriptedModelOutput:
    """End a scripted run without proposing patient responsibility."""

    return model_output(
        {
            "action": "request_manual_review",
            "reason": "Eligibility evidence requires human review.",
        }
    )


def make_finding() -> BaselineFinding:
    """Create an eligibility finding that requires external evidence."""

    return BaselineFinding(
        claim_id=CLAIM_ID,
        service_line_id=1,
        denial_codes=["PR-27"],
        root_cause=RootCause.REGISTRATION_ELIGIBILITY,
        baseline_disposition=Disposition.BILL_PATIENT,
        required_evidence=["eligibility_response"],
        rationale="Coverage status must be verified before patient billing.",
        filing_deadline=None,
        deadline_status="NONE",
    )


def make_context() -> ClaimToolContext:
    """Bind retrieval to the active member and approved payers."""

    return ClaimToolContext(
        claim_id=CLAIM_ID,
        payer="UHC",
        procedure_code="99213",
        service_date=date(2026, 6, 18),
        patient_account=PATIENT_ACCOUNT,
        eligibility_payers=("UHC", "AETNA"),
    )


def make_route(*, authorize: bool = True) -> RouteDecision:
    """Create an eligibility route with optional tool authorization."""

    return RouteDecision(
        claim_id=CLAIM_ID,
        service_line_id=1,
        outcome=RouteOutcome.AGENT_REQUIRED,
        reason_code="ELIGIBILITY_EVIDENCE_REQUIRED",
        routing_reason="Retrieve the missing eligibility response.",
        authorized_tools=[ELIGIBILITY_TOOL] if authorize else [],
    )


def run_case(
    outputs: list[ScriptedModelOutput],
    *,
    authorize: bool = True,
    eligibility_root: Path = ELIGIBILITY_ROOT,
):
    """Execute one eligibility-focused integrated case."""

    return run_integrated_case(
        run_id="eligibility-tool-runtime-test",
        system_instructions="Use only the active member's eligibility evidence.",
        model_client=ScriptedModelClient(outputs),
        dispatcher=ToolDispatcher(KnowledgeStore(KNOWLEDGE_ROOT)),
        baseline_finding=make_finding(),
        claim_context=make_context(),
        route_decision=make_route(authorize=authorize),
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
        eligibility_store=KnowledgeStore(eligibility_root),
    )


def event_names(result) -> list[str]:
    """Return the runtime trace's event names in order."""

    return [
        entry.event_name
        for entry in result.runtime_result.trace_entries
    ]


class EligibilityToolRuntimeTests(unittest.TestCase):
    """Exercise allowlisting, claim binding, and explicit no-match behavior."""

    def test_authorized_call_collects_eligibility_evidence(self) -> None:
        """An approved active-member request should enter the evidence store."""

        result = run_case([call_eligibility(), request_manual_review()])

        self.assertIn("TOOL_RETURNED", event_names(result))
        self.assertNotIn("TOOL_FAILED", event_names(result))
        self.assertTrue(result.retrieved_evidence)
        self.assertTrue(
            all(
                passage.source_type == "eligibility_record"
                for passage in result.retrieved_evidence
            )
        )

    def test_unauthorized_route_never_dispatches(self) -> None:
        """A route without eligibility permission must reject the request."""

        result = run_case([call_eligibility()], authorize=False)

        self.assertEqual(
            "tool_not_allowed",
            result.runtime_result.final_state.terminal_reason,
        )
        self.assertEqual(0, result.runtime_result.budget_usage.tool_calls_used)
        self.assertEqual((), result.retrieved_evidence)

    def test_another_member_stops_for_manual_review(self) -> None:
        """A cross-member request must fail at the tool boundary."""

        result = run_case(
            [call_eligibility(patient_account="PA-SYN-ANOTHER")]
        )

        self.assertEqual(
            RuntimePhase.MANUAL_REVIEW,
            result.runtime_result.final_state.phase,
        )
        self.assertEqual(
            "tool_boundary_error",
            result.runtime_result.final_state.terminal_reason,
        )
        self.assertIn("TOOL_FAILED", event_names(result))
        self.assertEqual((), result.retrieved_evidence)

    def test_approved_unknown_payer_remains_no_match(self) -> None:
        """An approved payer without a record must not produce guessed evidence."""

        result = run_case(
            [
                call_eligibility(payer="AETNA"),
                request_manual_review(),
            ]
        )

        returned_entry = next(
            entry
            for entry in result.runtime_result.trace_entries
            if entry.event_name == "TOOL_RETURNED"
        )
        self.assertEqual("NO_MATCH", returned_entry.details["error_code"])
        self.assertEqual((), result.retrieved_evidence)
        self.assertEqual(
            RuntimePhase.MANUAL_REVIEW,
            result.runtime_result.final_state.phase,
        )

    def test_listed_but_unavailable_record_remains_explicit(self) -> None:
        """A missing listed record must not be replaced with guessed evidence."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory) / "eligibility"
            shutil.copytree(ELIGIBILITY_ROOT, temporary_root)
            unavailable_record = (
                temporary_root
                / "records"
                / "ELIG-PA-SYN-10105-UHC.v1.json"
            )
            unavailable_record.unlink()

            result = run_case(
                [call_eligibility(), request_manual_review()],
                eligibility_root=temporary_root,
            )

        returned_entry = next(
            entry
            for entry in result.runtime_result.trace_entries
            if entry.event_name == "TOOL_RETURNED"
        )
        self.assertEqual(
            "DOCUMENT_UNAVAILABLE",
            returned_entry.details["error_code"],
        )
        self.assertEqual((), result.retrieved_evidence)
        self.assertEqual(
            RuntimePhase.MANUAL_REVIEW,
            result.runtime_result.final_state.phase,
        )


if __name__ == "__main__":
    unittest.main()
