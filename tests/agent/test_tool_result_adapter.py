"""Tests for translating shared tool contracts into runtime observations."""

from __future__ import annotations

import unittest
from datetime import date
from typing import Mapping

from claim_agent.agent.runtime_ports import ToolDispatcherPort
from claim_agent.agent.tool_result_adapter import (
    ToolResultDispatcherAdapter,
    adapt_tool_result,
)
from claim_agent.contracts import (
    ErrorCode,
    EvidencePassage,
    ToolResult,
    ToolStatus,
)


class RecordingSharedDispatcher:
    """Record one request and return a predefined shared result."""

    def __init__(self, result: ToolResult) -> None:
        """Store the result and initialize an empty request history."""

        self.result = result
        self.received_requests: list[tuple[str, str, Mapping[str, object]]] = []

    def dispatch(
        self,
        request_id: str,
        tool_name: str,
        tool_arguments: Mapping[str, object],
    ) -> ToolResult:
        """Record exactly what the runtime adapter forwarded."""

        self.received_requests.append(
            (
                request_id,
                tool_name,
                dict(tool_arguments),
            )
        )
        return self.result


def make_successful_result() -> ToolResult:
    """Create one contract-valid medical-policy retrieval result."""

    passage = EvidencePassage(
        passage_id="POL-BCBSAZ-MRI-BRAIN@v1#P1",
        source_id="POL-BCBSAZ-MRI-BRAIN",
        source_type="medical_policy",
        version="1",
        effective_from=date(2026, 1, 1),
        text="MRI may be medically necessary for a suspected mass.",
    )

    return ToolResult(
        request_id="run-1:tool:1",
        tool_name="retrieve_medical_policy",
        status=ToolStatus.OK,
        data={
            "criteria_formula": "A AND B",
            "knowledge_snapshot_id": "knowledge-v0",
        },
        source_ids=["POL-BCBSAZ-MRI-BRAIN@v1"],
        evidence_passages=[passage],
    )


class ToolResultAdapterTests(unittest.TestCase):
    """Verify that shared results retain identity, evidence, and errors."""

    def test_successful_result_preserves_identity_and_evidence(self) -> None:
        """Policy evidence should reach the runtime without losing fields."""

        observation = adapt_tool_result(make_successful_result())

        self.assertTrue(observation.succeeded)
        self.assertEqual(
            observation.payload["request_id"],
            "run-1:tool:1",
        )
        self.assertEqual(
            observation.payload["tool_name"],
            "retrieve_medical_policy",
        )
        self.assertEqual(
            observation.payload["data"]["criteria_formula"],
            "A AND B",
        )
        self.assertEqual(
            observation.payload["evidence_passages"][0]["passage_id"],
            "POL-BCBSAZ-MRI-BRAIN@v1#P1",
        )
        self.assertEqual(
            observation.source_ids,
            ("POL-BCBSAZ-MRI-BRAIN@v1",),
        )

    def test_failed_result_preserves_error_and_retryability(self) -> None:
        """The runtime must retain the dispatcher's failure instructions."""

        tool_result = ToolResult(
            request_id="run-1:tool:1",
            tool_name="retrieve_medical_policy",
            status=ToolStatus.ERROR,
            error_code=ErrorCode.TIMEOUT,
            retryable=True,
        )

        observation = adapt_tool_result(tool_result)

        self.assertFalse(observation.succeeded)
        self.assertEqual(observation.error_code, "TIMEOUT")
        self.assertTrue(observation.retryable)

    def test_adapter_forwards_the_exact_runtime_request(self) -> None:
        """The shared dispatcher should receive the runtime's request ID."""

        shared_dispatcher = RecordingSharedDispatcher(make_successful_result())
        adapter = ToolResultDispatcherAdapter(shared_dispatcher)

        adapter.dispatch(
            "run-1:tool:1",
            "retrieve_medical_policy",
            {
                "payer": "BCBS_AZ",
                "procedure_code": "70553",
            },
        )

        self.assertEqual(
            shared_dispatcher.received_requests,
            [
                (
                    "run-1:tool:1",
                    "retrieve_medical_policy",
                    {
                        "payer": "BCBS_AZ",
                        "procedure_code": "70553",
                    },
                )
            ],
        )

    def test_adapter_rejects_mismatched_result_identity(self) -> None:
        """Evidence from another request or tool must not enter this run."""

        adapter = ToolResultDispatcherAdapter(
            RecordingSharedDispatcher(make_successful_result())
        )

        mismatched_requests = (
            (
                "run-1:tool:2",
                "retrieve_medical_policy",
                "request_id",
            ),
            (
                "run-1:tool:1",
                "get_encounter_note",
                "tool_name",
            ),
        )

        for request_id, tool_name, expected_error in mismatched_requests:
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    adapter.dispatch(
                        request_id,
                        tool_name,
                        {},
                    )

    def test_adapter_satisfies_runtime_dispatcher_port(self) -> None:
        """The adapter should plug into the runtime without special handling."""

        adapter = ToolResultDispatcherAdapter(
            RecordingSharedDispatcher(make_successful_result())
        )

        self.assertIsInstance(adapter, ToolDispatcherPort)


if __name__ == "__main__":
    unittest.main()
