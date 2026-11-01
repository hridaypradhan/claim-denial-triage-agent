"""Tests for deterministic agent-runtime budget accounting."""

from __future__ import annotations

import unittest

from claim_agent.agent.runtime_budget import (
    RuntimeBudgetExceededError,
    RuntimeBudgetLimits,
    RuntimeBudgetUsage,
)


def make_test_limits() -> RuntimeBudgetLimits:
    """Create small limits that make boundary behavior easy to test."""

    return RuntimeBudgetLimits(
        max_model_calls=2,
        max_tool_calls=2,
        max_repair_attempts=1,
        max_total_steps=3,
    )


class RuntimeBudgetTests(unittest.TestCase):
    """Verify budget increments, exhaustion, and configuration validation."""

    def test_new_usage_starts_with_all_counters_at_zero(self) -> None:
        """A new claim run should not begin with consumed capacity."""

        usage = RuntimeBudgetUsage()

        self.assertEqual(usage.model_calls_used, 0)
        self.assertEqual(usage.tool_calls_used, 0)
        self.assertEqual(usage.repair_attempts_used, 0)
        self.assertEqual(usage.total_steps_used, 0)

    def test_model_call_consumes_model_and_total_step_budgets(self) -> None:
        """A planning call counts as both a model call and an external step."""

        updated_usage = RuntimeBudgetUsage().record_model_call(make_test_limits())

        self.assertEqual(updated_usage.model_calls_used, 1)
        self.assertEqual(updated_usage.tool_calls_used, 0)
        self.assertEqual(updated_usage.total_steps_used, 1)

    def test_tool_call_consumes_tool_and_total_step_budgets(self) -> None:
        """A policy or encounter lookup counts as a tool call and step."""

        updated_usage = RuntimeBudgetUsage().record_tool_call(make_test_limits())

        self.assertEqual(updated_usage.tool_calls_used, 1)
        self.assertEqual(updated_usage.model_calls_used, 0)
        self.assertEqual(updated_usage.total_steps_used, 1)

    def test_repair_consumes_repair_model_and_step_budgets(self) -> None:
        """Repairing malformed JSON requires another model call."""

        updated_usage = RuntimeBudgetUsage().record_repair_model_call(
            make_test_limits()
        )

        self.assertEqual(updated_usage.repair_attempts_used, 1)
        self.assertEqual(updated_usage.model_calls_used, 1)
        self.assertEqual(updated_usage.total_steps_used, 1)

    def test_model_call_limit_rejects_an_additional_call(self) -> None:
        """The agent must stop after the configured number of model calls."""

        limits = make_test_limits()
        usage = RuntimeBudgetUsage()
        usage = usage.record_model_call(limits)
        usage = usage.record_model_call(limits)

        with self.assertRaisesRegex(
            RuntimeBudgetExceededError,
            "model_call",
        ):
            usage.record_model_call(limits)

    def test_tool_call_limit_rejects_an_additional_call(self) -> None:
        """Repeated retrieval cannot exceed the configured tool limit."""

        limits = make_test_limits()
        usage = RuntimeBudgetUsage()
        usage = usage.record_tool_call(limits)
        usage = usage.record_tool_call(limits)

        with self.assertRaisesRegex(
            RuntimeBudgetExceededError,
            "tool_call",
        ):
            usage.record_tool_call(limits)

    def test_repair_limit_rejects_an_additional_repair(self) -> None:
        """Malformed output receives only the configured repair attempts."""

        limits = make_test_limits()
        usage = RuntimeBudgetUsage().record_repair_model_call(limits)

        with self.assertRaisesRegex(
            RuntimeBudgetExceededError,
            "repair_attempt",
        ):
            usage.record_repair_model_call(limits)

    def test_total_step_limit_applies_across_model_and_tool_calls(self) -> None:
        """Model and tool actions share one overall progress limit."""

        limits = RuntimeBudgetLimits(
            max_model_calls=3,
            max_tool_calls=3,
            max_repair_attempts=1,
            max_total_steps=2,
        )
        usage = RuntimeBudgetUsage()
        usage = usage.record_model_call(limits)
        usage = usage.record_tool_call(limits)

        with self.assertRaisesRegex(
            RuntimeBudgetExceededError,
            "total_step",
        ):
            usage.record_model_call(limits)

    def test_invalid_budget_limits_are_rejected(self) -> None:
        """A configuration with no usable model capacity is invalid."""

        with self.assertRaisesRegex(ValueError, "max_model_calls"):
            RuntimeBudgetLimits(
                max_model_calls=0,
                max_tool_calls=2,
                max_repair_attempts=0,
                max_total_steps=2,
            )

    def test_repair_limit_cannot_exceed_model_call_limit(self) -> None:
        """Every repair is a model call, so it cannot have a larger limit."""

        with self.assertRaisesRegex(
            ValueError,
            "max_repair_attempts",
        ):
            RuntimeBudgetLimits(
                max_model_calls=1,
                max_tool_calls=2,
                max_repair_attempts=2,
                max_total_steps=3,
            )


if __name__ == "__main__":
    unittest.main()
