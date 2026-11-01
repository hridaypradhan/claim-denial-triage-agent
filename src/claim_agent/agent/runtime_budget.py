"""Deterministic execution budgets for one agent run.

Budgets place hard limits on model calls, tool calls, repair attempts, and the
combined number of external actions. When a limit is exhausted, the future
runtime loop will convert the budget error into an explicit manual-review
result.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


class RuntimeBudgetExceededError(RuntimeError):
    """Raised when an action would exceed a configured runtime budget."""

    def __init__(self, budget_name: str, limit: int) -> None:
        """Describe the exhausted budget in a machine-readable form."""

        self.budget_name = budget_name
        self.limit = limit

        super().__init__(
            f"Cannot continue because the {budget_name} budget "
            f"has reached its limit of {limit}."
        )


@dataclass(frozen=True)
class RuntimeBudgetLimits:
    """Maximum external actions permitted during one agent run.

    A repair attempt is also a model call, so recording a repair consumes the
    repair, model-call, and total-step budgets together.
    """

    max_model_calls: int
    max_tool_calls: int
    max_repair_attempts: int
    max_total_steps: int

    def __post_init__(self) -> None:
        """Reject limits that would create an unusable runtime configuration."""

        positive_limits = {
            "max_model_calls": self.max_model_calls,
            "max_tool_calls": self.max_tool_calls,
            "max_total_steps": self.max_total_steps,
        }

        for limit_name, limit_value in positive_limits.items():
            if limit_value <= 0:
                raise ValueError(f"{limit_name} must be greater than zero")

        if self.max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be zero or greater")

        if self.max_repair_attempts > self.max_model_calls:
            raise ValueError("max_repair_attempts cannot exceed max_model_calls")


@dataclass(frozen=True)
class RuntimeBudgetUsage:
    """Number of budgeted actions already consumed by one run."""

    model_calls_used: int = 0
    tool_calls_used: int = 0
    repair_attempts_used: int = 0
    total_steps_used: int = 0

    def record_model_call(
        self,
        limits: RuntimeBudgetLimits,
    ) -> "RuntimeBudgetUsage":
        """Return updated usage after one ordinary model call."""

        self._ensure_capacity(
            budget_name="model_call",
            amount_used=self.model_calls_used,
            limit=limits.max_model_calls,
        )
        self._ensure_total_step_capacity(limits)

        return replace(
            self,
            model_calls_used=self.model_calls_used + 1,
            total_steps_used=self.total_steps_used + 1,
        )

    def record_tool_call(
        self,
        limits: RuntimeBudgetLimits,
    ) -> "RuntimeBudgetUsage":
        """Return updated usage after one tool invocation."""

        self._ensure_capacity(
            budget_name="tool_call",
            amount_used=self.tool_calls_used,
            limit=limits.max_tool_calls,
        )
        self._ensure_total_step_capacity(limits)

        return replace(
            self,
            tool_calls_used=self.tool_calls_used + 1,
            total_steps_used=self.total_steps_used + 1,
        )

    def record_repair_model_call(
        self,
        limits: RuntimeBudgetLimits,
    ) -> "RuntimeBudgetUsage":
        """Return updated usage after a model call that repairs bad output.

        This operation is atomic: all affected budgets are checked before any
        counter is increased. A failed check therefore leaves the original
        immutable usage object unchanged.
        """

        self._ensure_capacity(
            budget_name="repair_attempt",
            amount_used=self.repair_attempts_used,
            limit=limits.max_repair_attempts,
        )
        self._ensure_capacity(
            budget_name="model_call",
            amount_used=self.model_calls_used,
            limit=limits.max_model_calls,
        )
        self._ensure_total_step_capacity(limits)

        return replace(
            self,
            model_calls_used=self.model_calls_used + 1,
            repair_attempts_used=self.repair_attempts_used + 1,
            total_steps_used=self.total_steps_used + 1,
        )

    def _ensure_total_step_capacity(
        self,
        limits: RuntimeBudgetLimits,
    ) -> None:
        """Check the combined model-and-tool action limit."""

        self._ensure_capacity(
            budget_name="total_step",
            amount_used=self.total_steps_used,
            limit=limits.max_total_steps,
        )

    @staticmethod
    def _ensure_capacity(
        budget_name: str,
        amount_used: int,
        limit: int,
    ) -> None:
        """Raise a descriptive error when no capacity remains."""

        if amount_used >= limit:
            raise RuntimeBudgetExceededError(
                budget_name=budget_name,
                limit=limit,
            )
