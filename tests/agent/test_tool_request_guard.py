"""Tests for preventing retrieval with another claim's arguments."""

from __future__ import annotations

import unittest
from datetime import date
from typing import Mapping

from claim_agent.agent.tool_request_guard import (
    ClaimBoundToolDispatcher,
    ClaimToolContext,
    ToolArgumentMismatchError,
)
from claim_agent.contracts import ToolResult, ToolStatus
from claim_agent.tools.dispatcher import (
    ENCOUNTER_NOTE_TOOL,
    MEDICAL_POLICY_TOOL,
)

CLAIM_ID = "CLM-2026-005097"
PAYER = "BCBS_AZ"
PROCEDURE_CODE = "70553"
SERVICE_DATE = date(2026, 6, 2)


class RecordingDispatcher:
    """Record requests that pass the claim-bound argument guard."""

    def __init__(self) -> None:
        """Start with no forwarded requests."""

        self.received_requests: list[tuple[str, str, Mapping[str, object]]] = []

    def dispatch(
        self,
        request_id: str,
        tool_name: str,
        tool_arguments: Mapping[str, object],
    ) -> ToolResult:
        """Record a request and return a minimal successful result."""

        self.received_requests.append(
            (
                request_id,
                tool_name,
                dict(tool_arguments),
            )
        )

        return ToolResult(
            request_id=request_id,
            tool_name=tool_name,
            status=ToolStatus.OK,
        )


def make_guard(
    dispatcher: RecordingDispatcher,
) -> ClaimBoundToolDispatcher:
    """Create a guard for the synthetic claim used by these tests."""

    return ClaimBoundToolDispatcher(
        dispatcher=dispatcher,
        claim_context=ClaimToolContext(
            claim_id=CLAIM_ID,
            payer=PAYER,
            procedure_code=PROCEDURE_CODE,
            service_date=SERVICE_DATE,
        ),
    )


def make_policy_arguments() -> dict[str, object]:
    """Return policy arguments that belong to the active claim."""

    return {
        "payer": PAYER,
        "procedure_code": PROCEDURE_CODE,
        "service_date": SERVICE_DATE.isoformat(),
        "query": "headache diplopia suspected mass",
    }


class ClaimBoundToolDispatcherTests(unittest.TestCase):
    """Verify that tools cannot retrieve evidence for another claim."""

    def test_matching_policy_request_reaches_dispatcher(self) -> None:
        """A request matching the active claim should proceed normally."""

        dispatcher = RecordingDispatcher()
        guard = make_guard(dispatcher)
        policy_arguments = make_policy_arguments()

        result = guard.dispatch(
            request_id="run-1:tool:1",
            tool_name=MEDICAL_POLICY_TOOL,
            tool_arguments=policy_arguments,
        )

        self.assertEqual(ToolStatus.OK, result.status)
        self.assertEqual(
            [
                (
                    "run-1:tool:1",
                    MEDICAL_POLICY_TOOL,
                    policy_arguments,
                )
            ],
            dispatcher.received_requests,
        )

    def test_policy_request_rejects_another_procedure(self) -> None:
        """A 70551 policy must not be used for a 70553 claim."""

        dispatcher = RecordingDispatcher()
        guard = make_guard(dispatcher)
        policy_arguments = make_policy_arguments()
        policy_arguments["procedure_code"] = "70551"

        with self.assertRaisesRegex(
            ToolArgumentMismatchError,
            "procedure_code",
        ):
            guard.dispatch(
                request_id="run-1:tool:1",
                tool_name=MEDICAL_POLICY_TOOL,
                tool_arguments=policy_arguments,
            )

        self.assertEqual([], dispatcher.received_requests)

    def test_policy_request_rejects_another_payer(self) -> None:
        """An Aetna policy must not be used for a BCBS claim."""

        dispatcher = RecordingDispatcher()
        guard = make_guard(dispatcher)
        policy_arguments = make_policy_arguments()
        policy_arguments["payer"] = "AETNA"

        with self.assertRaisesRegex(
            ToolArgumentMismatchError,
            "payer",
        ):
            guard.dispatch(
                request_id="run-1:tool:1",
                tool_name=MEDICAL_POLICY_TOOL,
                tool_arguments=policy_arguments,
            )

        self.assertEqual([], dispatcher.received_requests)

    def test_policy_request_rejects_another_service_date(self) -> None:
        """A policy lookup must use the active claim's service date."""

        dispatcher = RecordingDispatcher()
        guard = make_guard(dispatcher)
        policy_arguments = make_policy_arguments()
        policy_arguments["service_date"] = "2025-06-02"

        with self.assertRaisesRegex(
            ToolArgumentMismatchError,
            "service_date",
        ):
            guard.dispatch(
                request_id="run-1:tool:1",
                tool_name=MEDICAL_POLICY_TOOL,
                tool_arguments=policy_arguments,
            )

        self.assertEqual([], dispatcher.received_requests)

    def test_encounter_request_rejects_another_claim(self) -> None:
        """Another claim's encounter note must not enter this run."""

        dispatcher = RecordingDispatcher()
        guard = make_guard(dispatcher)

        with self.assertRaisesRegex(
            ToolArgumentMismatchError,
            "claim_id",
        ):
            guard.dispatch(
                request_id="run-1:tool:1",
                tool_name=ENCOUNTER_NOTE_TOOL,
                tool_arguments={
                    "claim_id": "CLM-ANOTHER-PATIENT",
                    "document_id": "ENC-ANOTHER-PATIENT-PN",
                },
            )

        self.assertEqual([], dispatcher.received_requests)


if __name__ == "__main__":
    unittest.main()
