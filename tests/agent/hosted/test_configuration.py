"""Tests for reproducible hosted run configuration."""

from __future__ import annotations

import unittest

from claim_agent.agent.hosted.configuration import (
    build_hosted_run_configuration,
)
from claim_agent.agent.hosted.factory import (
    SELECTED_VOYAGER_PROFILE,
)
from claim_agent.agent.planning_prompt import PLANNING_PROMPT_VERSION
from claim_agent.agent.run_configuration import (
    AllowedTool,
    calculate_run_configuration_sha256,
)
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace
from claim_agent.agent.trace_adapter import CanonicalTraceAdapter
from claim_agent.contracts import RouteDecision, RouteOutcome


class HostedRunConfigurationTests(unittest.TestCase):
    """Verify hosted settings are complete, stable, and non-secret."""

    def setUp(self) -> None:
        """Create one representative hosted run configuration."""

        self.configuration = build_hosted_run_configuration(
            code_commit="abc123",
            knowledge_snapshot_id="knowledge-v1",
            allowed_tools=(
                AllowedTool(
                    tool_name="retrieve_medical_policy",
                    tool_version="0.1",
                ),
                AllowedTool(
                    tool_name="get_encounter_note",
                    tool_version="0.1",
                ),
            ),
            budget_limits=RuntimeBudgetLimits(
                max_model_calls=4,
                max_tool_calls=3,
                max_repair_attempts=1,
                max_total_steps=7,
                max_runtime_seconds=30.0,
            ),
        )

    def test_configuration_records_hosted_identity(self) -> None:
        """Provider, model, and prompt identities must be traceable."""

        self.assertEqual(
            "0.3",
            self.configuration.config_version,
        )
        self.assertEqual(
            SELECTED_VOYAGER_PROFILE.provider_name,
            self.configuration.model_provider,
        )
        self.assertEqual(
            SELECTED_VOYAGER_PROFILE.model_id,
            self.configuration.model_id,
        )
        self.assertEqual(
            PLANNING_PROMPT_VERSION,
            self.configuration.prompt_version,
        )

    def test_configuration_records_provider_options(self) -> None:
        """Options affecting hosted behavior must not remain implicit."""

        options = self.configuration.model_parameters.provider_options

        self.assertIsNotNone(options)
        assert options is not None

        self.assertEqual(
            SELECTED_VOYAGER_PROFILE.reasoning_effort,
            options.reasoning_effort,
        )
        self.assertEqual(
            SELECTED_VOYAGER_PROFILE.response_format_mode,
            options.response_format_mode,
        )
        self.assertIs(
            False,
            options.gateway_addons_enabled,
        )

    def test_configuration_records_runtime_limits(self) -> None:
        """The trace configuration must reproduce execution limits."""

        self.assertEqual(4, self.configuration.max_model_calls)
        self.assertEqual(3, self.configuration.max_tool_calls)
        self.assertEqual(1, self.configuration.max_repair_attempts)
        self.assertEqual(7, self.configuration.max_total_steps)
        self.assertEqual(
            SELECTED_VOYAGER_PROFILE.request_timeout_seconds,
            self.configuration.model_request_timeout_seconds,
        )

    def test_allowed_tools_are_sorted(self) -> None:
        """Caller ordering must not create unstable configuration hashes."""

        self.assertEqual(
            (
                "get_encounter_note",
                "retrieve_medical_policy",
            ),
            tuple(tool.tool_name for tool in self.configuration.allowed_tools),
        )

    def test_configuration_hash_is_stable(self) -> None:
        """Equivalent hosted settings must produce the same fingerprint."""

        repeated_configuration = build_hosted_run_configuration(
            code_commit="abc123",
            knowledge_snapshot_id="knowledge-v1",
            allowed_tools=tuple(reversed(self.configuration.allowed_tools)),
            budget_limits=RuntimeBudgetLimits(
                max_model_calls=4,
                max_tool_calls=3,
                max_repair_attempts=1,
                max_total_steps=7,
                max_runtime_seconds=30.0,
            ),
        )

        self.assertEqual(
            calculate_run_configuration_sha256(self.configuration),
            calculate_run_configuration_sha256(repeated_configuration),
        )

    def test_configuration_contains_no_credential(self) -> None:
        """API keys must never enter persisted run metadata."""

        serialized = self.configuration.model_dump_json()

        self.assertNotIn("CLAIM_AGENT_LLM_API_KEY", serialized)
        self.assertNotIn("synthetic-voyager-key", serialized)

    def test_trace_header_contains_hosted_provider_options(self) -> None:
        """RUN_STARTED should persist the complete hosted configuration."""

        route = RouteDecision(
            claim_id="CLM-HOSTED-001",
            service_line_id=1,
            outcome=RouteOutcome.AGENT_REQUIRED,
            reason_code="EVIDENCE_EVALUATION_REQUIRED",
            routing_reason="Synthetic hosted configuration test.",
            authorized_tools=[
                "retrieve_medical_policy",
                "get_encounter_note",
            ],
        )
        runtime_trace = InMemoryRuntimeTrace(run_id="hosted-run-1")
        runtime_trace.record("RUN_STARTED")

        event = CanonicalTraceAdapter(
            run_configuration=self.configuration,
            route_decision=route,
        ).adapt(runtime_trace.entries)[0]

        model_parameters = event.payload["config"]["model_parameters"]
        self.assertEqual(
            "none",
            model_parameters["provider_options"]["reasoning_effort"],
        )
        self.assertIs(
            False,
            model_parameters["provider_options"][
                "gateway_addons_enabled"
            ],
        )


if __name__ == "__main__":
    unittest.main()
