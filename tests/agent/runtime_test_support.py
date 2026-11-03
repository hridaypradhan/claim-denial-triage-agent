"""Shared builders for offline runtime test suites."""

from claim_agent.agent.runtime_budget import RuntimeBudgetLimits


def make_test_limits() -> RuntimeBudgetLimits:
    """Provide compact limits for deterministic runtime tests."""

    return RuntimeBudgetLimits(
        max_model_calls=2,
        max_tool_calls=2,
        max_repair_attempts=1,
        max_total_steps=3,
    )
