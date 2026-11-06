"""Tests for the production-shaped integrated runtime entry point."""

from __future__ import annotations

import json
import unittest
from datetime import date
from unittest.mock import Mock

from claim_agent.agent.hosted_model import (
    HostedModelClient,
    HostedModelTransportRequest,
    HostedModelTransportResponse,
)
from claim_agent.agent.integrated_runtime import run_integrated_case
from claim_agent.agent.model_interface import ModelClient
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from claim_agent.agent.runtime_case import AgentRunNotAuthorizedError
from claim_agent.agent.runtime_state import RuntimePhase
from claim_agent.agent.scripted_model import (
    ScriptedModelClient,
    ScriptedModelOutput,
)
from claim_agent.agent.tool_result_adapter import SharedToolDispatcher
from claim_agent.contracts import (
    BaselineFinding,
    Disposition,
    RootCause,
    RouteDecision,
    RouteOutcome,
)

CLAIM_ID = "CLM-INTEGRATED-RUNTIME-001"
AUTHORIZED_TOOL = "retrieve_medical_policy"


def make_baseline_finding() -> BaselineFinding:
    """Create one finding that requires evidence interpretation."""

    return BaselineFinding(
        claim_id=CLAIM_ID,
        service_line_id=1,
        denial_codes=["CO-50"],
        root_cause=RootCause.MEDICAL_NECESSITY,
        baseline_disposition=Disposition.APPEAL_WITH_DOCUMENTATION,
        required_evidence=["medical_policy"],
        rationale="The applicable policy must be interpreted.",
        filing_deadline=date(2026, 12, 31),
        deadline_status="ACTIVE",
    )


def make_route_decision(
    outcome: RouteOutcome = RouteOutcome.AGENT_REQUIRED,
) -> RouteDecision:
    """Create a route for the same synthetic claim service line."""

    authorized_tools = (
        [AUTHORIZED_TOOL] if outcome is RouteOutcome.AGENT_REQUIRED else []
    )

    return RouteDecision(
        claim_id=CLAIM_ID,
        service_line_id=1,
        outcome=outcome,
        reason_code="EVIDENCE_EVALUATION_REQUIRED",
        routing_reason="Policy evidence requires interpretation.",
        authorized_tools=authorized_tools,
    )


def make_budget_limits() -> RuntimeBudgetLimits:
    """Create small deterministic limits for entry-point tests."""

    return RuntimeBudgetLimits(
        max_model_calls=1,
        max_tool_calls=1,
        max_repair_attempts=0,
        max_total_steps=1,
    )


class TimeoutHostedTransport:
    """Record the hosted request and simulate a provider timeout."""

    def __init__(self) -> None:
        """Start with no recorded requests."""

        self.received_requests: list[HostedModelTransportRequest] = []

    def generate(
        self,
        request: HostedModelTransportRequest,
    ) -> HostedModelTransportResponse:
        """Record the request before simulating an external timeout."""

        self.received_requests.append(request)
        raise TimeoutError("The synthetic hosted provider timed out")


class IntegratedRuntimeTests(unittest.TestCase):
    """Verify authorization before execution and correct runtime assembly."""

    def test_hosted_timeout_produces_audited_manual_review(self) -> None:
        """A hosted timeout should stop safely and preserve its audit trail."""

        transport = TimeoutHostedTransport()
        model_client = HostedModelClient(
            transport=transport,
            model_name="test-hosted-model",
            timeout_seconds=2.5,
        )
        dispatcher = Mock(spec=SharedToolDispatcher)

        result = run_integrated_case(
            run_id="integrated-hosted-timeout",
            system_instructions=(
                "Use only retrieved evidence and return structured JSON."
            ),
            model_client=model_client,
            dispatcher=dispatcher,
            baseline_finding=make_baseline_finding(),
            route_decision=make_route_decision(),
            budget_limits=make_budget_limits(),
            as_of=date(2026, 11, 6),
            package_version="test-v1",
            knowledge_snapshot_id="test-snapshot",
        )

        self.assertEqual(
            RuntimePhase.MANUAL_REVIEW,
            result.runtime_result.final_state.phase,
        )
        self.assertEqual(
            "model_timeout",
            result.runtime_result.final_state.terminal_reason,
        )
        self.assertEqual(
            1,
            result.runtime_result.budget_usage.model_calls_used,
        )
        self.assertEqual(
            1,
            result.runtime_result.budget_usage.total_steps_used,
        )

        trace_event_names = [
            trace_entry.event_name
            for trace_entry in result.runtime_result.trace_entries
        ]
        self.assertIn("MODEL_REQUESTED", trace_event_names)
        self.assertIn("MODEL_FAILED", trace_event_names)
        self.assertNotIn("MODEL_RESPONDED", trace_event_names)

        self.assertEqual(
            Disposition.MANUAL_REVIEW,
            result.output_package.final_disposition,
        )
        self.assertEqual(
            "model_timeout",
            result.output_package.agent_terminal_reason,
        )
        self.assertIn(
            "model_timeout",
            result.output_package.manual_review_reasons,
        )

        self.assertEqual(1, len(transport.received_requests))
        self.assertEqual(
            2.5,
            transport.received_requests[0].timeout_seconds,
        )
        self.assertEqual(
            (AUTHORIZED_TOOL,),
            transport.received_requests[0].allowed_tool_names,
        )

        dispatcher.dispatch.assert_not_called()
        self.assertIsNone(result.proposed_decision)
        self.assertIsNone(result.validation_result)

    def test_authorized_case_reaches_model_with_typed_context(self) -> None:
        """An agent route should provide approved context and tools."""

        model_client = ScriptedModelClient(
            [
                ScriptedModelOutput(
                    response_text=json.dumps(
                        {
                            "action": "request_manual_review",
                            "reason": "Policy evidence has not been retrieved.",
                        }
                    )
                )
            ]
        )
        dispatcher = Mock(spec=SharedToolDispatcher)

        result = run_integrated_case(
            run_id="integrated-authorized-case",
            system_instructions=(
                "Use only retrieved evidence and request manual review "
                "when evidence is unavailable."
            ),
            model_client=model_client,
            dispatcher=dispatcher,
            baseline_finding=make_baseline_finding(),
            route_decision=make_route_decision(),
            budget_limits=make_budget_limits(),
            as_of=date(2026, 11, 6),
            package_version="test-v1",
            knowledge_snapshot_id="test-snapshot",
        )

        self.assertEqual(
            RuntimePhase.MANUAL_REVIEW,
            result.runtime_result.final_state.phase,
        )
        self.assertEqual(
            "integrated-authorized-case",
            result.output_package.run_id,
        )
        self.assertEqual(
            Disposition.MANUAL_REVIEW,
            result.output_package.final_disposition,
        )
        self.assertEqual(
            "model_requested_manual_review",
            result.output_package.agent_terminal_reason,
        )
        self.assertEqual(
            "model_requested_manual_review",
            result.runtime_result.final_state.terminal_reason,
        )
        self.assertEqual(1, len(model_client.received_requests))

        model_request = model_client.received_requests[0]
        self.assertIn(
            "BEGIN DETERMINISTIC CASE DATA",
            model_request.task_context,
        )
        self.assertIn(
            CLAIM_ID,
            model_request.task_context,
        )
        self.assertEqual(
            (AUTHORIZED_TOOL,),
            model_request.allowed_tool_names,
        )

        dispatcher.dispatch.assert_not_called()
        self.assertEqual((), result.retrieved_evidence)
        self.assertIsNone(result.policy_formula)
        self.assertIsNone(result.proposed_decision)
        self.assertIsNone(result.validation_result)

    def test_non_agent_routes_stop_before_model_or_tools(self) -> None:
        """Routes owned by deterministic or human handling must bypass AI."""

        blocked_outcomes = (
            RouteOutcome.DETERMINISTIC_ONLY,
            RouteOutcome.MANUAL_REVIEW,
        )

        for blocked_outcome in blocked_outcomes:
            with self.subTest(blocked_outcome=blocked_outcome):
                model_client = Mock(spec=ModelClient)
                dispatcher = Mock(spec=SharedToolDispatcher)

                with self.assertRaisesRegex(
                    AgentRunNotAuthorizedError,
                    blocked_outcome.value,
                ):
                    run_integrated_case(
                        run_id="integrated-blocked-case",
                        system_instructions="This instruction must not be used.",
                        model_client=model_client,
                        dispatcher=dispatcher,
                        baseline_finding=make_baseline_finding(),
                        route_decision=make_route_decision(blocked_outcome),
                        budget_limits=make_budget_limits(),
                        as_of=date(2026, 11, 6),
                        package_version="test-v1",
                        knowledge_snapshot_id="test-snapshot",
                    )

                model_client.generate_response.assert_not_called()
                dispatcher.dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
