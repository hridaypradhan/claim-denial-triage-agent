"""Tests for preparing typed baseline and routing inputs."""

from __future__ import annotations

import json
import unittest
from datetime import date

from claim_agent.agent.runtime_case import (
    AgentRunNotAuthorizedError,
    prepare_runtime_case,
)
from claim_agent.contracts import (
    BaselineFinding,
    Disposition,
    RootCause,
    RouteDecision,
    RouteOutcome,
)

CLAIM_ID = "CLM-RUNTIME-CASE-001"


def make_baseline_finding() -> BaselineFinding:
    """Create one evidence-dependent baseline finding."""

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
        rationale="Evidence interpretation is required.",
        filing_deadline=date(2026, 12, 31),
        deadline_status="ACTIVE",
    )


def make_route_decision(
    outcome: RouteOutcome = RouteOutcome.AGENT_REQUIRED,
    authorized_tools: list[str] | None = None,
) -> RouteDecision:
    """Create a route for the same synthetic service line."""

    if authorized_tools is None:
        authorized_tools = [
            "retrieve_medical_policy",
            "get_encounter_note",
        ]

    return RouteDecision(
        claim_id=CLAIM_ID,
        service_line_id=1,
        outcome=outcome,
        reason_code="EVIDENCE_EVALUATION_REQUIRED",
        routing_reason="The agent must interpret retrieved evidence.",
        authorized_tools=authorized_tools,
    )


class RuntimeCaseTests(unittest.TestCase):
    """Verify route enforcement, identity, and deterministic context."""

    def test_agent_route_prepares_context_and_tool_allowlist(self) -> None:
        """An authorized case should become immutable runtime inputs."""

        prepared_case = prepare_runtime_case(
            baseline_finding=make_baseline_finding(),
            route_decision=make_route_decision(),
        )

        self.assertEqual(
            (
                "retrieve_medical_policy",
                "get_encounter_note",
            ),
            prepared_case.allowed_tool_names,
        )
        self.assertIn(
            "BEGIN DETERMINISTIC CASE DATA",
            prepared_case.task_context,
        )
        self.assertIn(
            "END DETERMINISTIC CASE DATA",
            prepared_case.task_context,
        )

    def test_context_contains_both_shared_contracts(self) -> None:
        """The model context should preserve finding and routing fields."""

        prepared_case = prepare_runtime_case(
            baseline_finding=make_baseline_finding(),
            route_decision=make_route_decision(),
        )

        serialized_data = prepared_case.task_context.split(
            "BEGIN DETERMINISTIC CASE DATA\n",
            maxsplit=1,
        )[1].split("\nEND DETERMINISTIC CASE DATA", maxsplit=1,)[0]
        case_data = json.loads(serialized_data)

        self.assertEqual(
            CLAIM_ID,
            case_data["baseline_finding"]["claim_id"],
        )
        self.assertEqual(
            "AGENT_REQUIRED",
            case_data["route_decision"]["outcome"],
        )

    def test_trusted_tool_context_is_serialized_as_case_data(
        self,
    ) -> None:
        """Claim-bound tool metadata should reach the planning model."""

        prepared_case = prepare_runtime_case(
            baseline_finding=make_baseline_finding(),
            route_decision=make_route_decision(),
            tool_context={
                "get_encounter_note": {
                    "claim_id": "CLM-TEST",
                    "available_document_ids": ["ENC-TEST-CN"],
                }
            },
        )

        self.assertIn(
            '"available_document_ids":["ENC-TEST-CN"]',
            prepared_case.task_context,
        )

    def test_context_serialization_is_deterministic(self) -> None:
        """Equivalent typed inputs should produce identical model context."""

        finding = make_baseline_finding()
        route = make_route_decision()

        first_case = prepare_runtime_case(finding, route)
        second_case = prepare_runtime_case(finding, route)

        self.assertEqual(
            first_case.task_context,
            second_case.task_context,
        )

    def test_claim_identity_mismatch_is_rejected(self) -> None:
        """A route for another claim must not start this runtime."""

        mismatched_route = make_route_decision().model_copy(
            update={"claim_id": "CLM-WRONG"}
        )

        with self.assertRaisesRegex(ValueError, "claim_id"):
            prepare_runtime_case(
                make_baseline_finding(),
                mismatched_route,
            )

    def test_service_line_identity_mismatch_is_rejected(self) -> None:
        """A route for another service line must not start this runtime."""

        mismatched_route = make_route_decision().model_copy(
            update={"service_line_id": 2}
        )

        with self.assertRaisesRegex(ValueError, "service_line_id"):
            prepare_runtime_case(
                make_baseline_finding(),
                mismatched_route,
            )

    def test_non_agent_routes_are_rejected(self) -> None:
        """Deterministic and manual routes must bypass model execution."""

        blocked_outcomes = (
            RouteOutcome.DETERMINISTIC_ONLY,
            RouteOutcome.MANUAL_REVIEW,
        )

        for outcome in blocked_outcomes:
            with self.subTest(outcome=outcome):
                with self.assertRaisesRegex(
                    AgentRunNotAuthorizedError,
                    outcome.value,
                ):
                    prepare_runtime_case(
                        make_baseline_finding(),
                        make_route_decision(
                            outcome=outcome,
                            authorized_tools=[],
                        ),
                    )

    def test_blank_authorized_tool_is_rejected(self) -> None:
        """A blank tool name must never enter the runtime allowlist."""

        with self.assertRaisesRegex(ValueError, "blank"):
            prepare_runtime_case(
                make_baseline_finding(),
                make_route_decision(
                    authorized_tools=[
                        "retrieve_medical_policy",
                        " ",
                    ]
                ),
            )

    def test_duplicate_authorized_tool_is_rejected(self) -> None:
        """Duplicate tools would make allowlist behavior ambiguous."""

        with self.assertRaisesRegex(ValueError, "duplicate"):
            prepare_runtime_case(
                make_baseline_finding(),
                make_route_decision(
                    authorized_tools=[
                        "retrieve_medical_policy",
                        "retrieve_medical_policy",
                    ]
                ),
            )


if __name__ == "__main__":
    unittest.main()
