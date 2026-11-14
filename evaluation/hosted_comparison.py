"""Validate the frozen development-only hosted-model comparison plan.

This module does not make model calls. Live execution is added separately so
the plan can be reviewed and tested before any Voyager usage occurs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from claim_agent.agent.hosted.factory import HostedModelProfile
from claim_agent.agent.planning_prompt import PLANNING_PROMPT_VERSION
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from evaluation.pilot_data import REPO_ROOT, load_dataset, load_labels

DEFAULT_HOSTED_COMPARISON_PLAN = (
    REPO_ROOT / "evaluation" / "hosted_comparison_plan.json"
)


class HostedComparisonBudgets(BaseModel):
    """Frozen runtime and retry limits shared by every candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_model_calls: int
    max_tool_calls: int
    max_repair_attempts: int
    max_total_steps: int
    max_runtime_seconds: float
    max_tool_retries: int
    tool_request_timeout_seconds: float

    def runtime_limits(self) -> RuntimeBudgetLimits:
        """Translate persisted values into the runtime budget contract."""

        return RuntimeBudgetLimits(
            max_model_calls=self.max_model_calls,
            max_tool_calls=self.max_tool_calls,
            max_repair_attempts=self.max_repair_attempts,
            max_total_steps=self.max_total_steps,
            max_runtime_seconds=self.max_runtime_seconds,
        )


class HostedCandidate(BaseModel):
    """One exact Voyager model configuration under comparison."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: str
    base_url: str
    model_id: str
    max_completion_tokens: int
    reasoning_effort: str
    temperature: float
    response_format_mode: str
    request_timeout_seconds: float
    disable_gateway_addons: bool

    def hosted_profile(self) -> HostedModelProfile:
        """Build the same profile used by the hosted client factory."""

        return HostedModelProfile(**self.model_dump())


class HostedComparisonPlan(BaseModel):
    """Reviewed development experiment that must precede live calls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_version: str
    phase: Literal["comparison", "validation"]
    dataset_manifest: str
    labels: str
    knowledge_snapshot_id: str
    prompt_version: str
    repetitions: int
    case_ids: tuple[str, ...]
    budgets: HostedComparisonBudgets
    ranking_order: tuple[str, ...]
    tie_breakers: tuple[str, ...]
    candidates: tuple[HostedCandidate, ...]

    @field_validator("repetitions")
    @classmethod
    def require_three_repetitions(cls, repetitions: int) -> int:
        """The accepted selection procedure requires three repeat runs."""

        if repetitions != 3:
            raise ValueError("repetitions must equal 3")

        return repetitions

    @field_validator("case_ids")
    @classmethod
    def require_representative_case_count(
        cls,
        case_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Use the accepted six-to-ten-case development comparison."""

        if not 6 <= len(case_ids) <= 10:
            raise ValueError("case_ids must contain 6 to 10 cases")

        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_ids must not contain duplicates")

        return case_ids

    @field_validator("candidates")
    @classmethod
    def require_unique_candidates(
        cls,
        candidates: tuple[HostedCandidate, ...],
    ) -> tuple[HostedCandidate, ...]:
        """Prevent duplicate model runs from appearing as comparisons."""

        model_ids = [candidate.model_id for candidate in candidates]

        if len(model_ids) != len(set(model_ids)):
            raise ValueError("candidate model IDs must be unique")

        return candidates

    @model_validator(mode="after")
    def require_current_development_inputs(
        self,
    ) -> "HostedComparisonPlan":
        """Reject invalid phase, data, or prompt combinations."""

        if self.phase == "comparison" and len(self.candidates) < 2:
            raise ValueError(
                "comparison plans require at least two candidate models"
            )

        if self.phase == "validation" and len(self.candidates) != 1:
            raise ValueError(
                "validation plans require exactly one gated model"
            )

        if "development" not in self.dataset_manifest:
            raise ValueError("dataset_manifest must reference development data")

        if "development" not in self.labels:
            raise ValueError("labels must reference development data")

        if self.prompt_version != PLANNING_PROMPT_VERSION:
            raise ValueError("prompt_version must match the frozen planning prompt")

        return self

    def manifest_path(self) -> Path:
        """Resolve the reviewed manifest relative to the repository."""

        return REPO_ROOT / self.dataset_manifest

    def labels_path(self) -> Path:
        """Resolve the reviewed labels relative to the repository."""

        return REPO_ROOT / self.labels


def load_hosted_comparison_plan(
    path: Path = DEFAULT_HOSTED_COMPARISON_PLAN,
) -> HostedComparisonPlan:
    """Load and cross-check the plan against its development manifest."""

    plan = HostedComparisonPlan.model_validate_json(path.read_text(encoding="utf-8"))
    dataset = load_dataset(plan.manifest_path())
    manifest_case_ids = {case.case_id for case in dataset.cases}
    missing_case_ids = set(plan.case_ids) - manifest_case_ids

    if missing_case_ids:
        raise ValueError(
            "comparison cases missing from development manifest: "
            + ", ".join(sorted(missing_case_ids))
        )

    if not plan.labels_path().is_file():
        raise ValueError("comparison labels file does not exist")

    manifest_values = json.loads(
        plan.manifest_path().read_text(encoding="utf-8")
    )
    label_values = json.loads(
        plan.labels_path().read_text(encoding="utf-8")
    )
    for source_name, source_values in (
        ("manifest", manifest_values),
        ("labels", label_values),
    ):
        if (
            source_values.get("knowledge_snapshot_id")
            != plan.knowledge_snapshot_id
        ):
            raise ValueError(
                "comparison knowledge snapshot does not match "
                f"{source_name}"
            )

    load_labels(plan.labels_path(), dataset)

    return plan


def main() -> int:
    """Validate the frozen plan without making hosted requests."""

    plan = load_hosted_comparison_plan()
    request_count = len(plan.candidates) * len(plan.case_ids) * plan.repetitions

    print(
        json.dumps(
            {
                "plan_version": plan.plan_version,
                "candidate_model_ids": [
                    candidate.model_id for candidate in plan.candidates
                ],
                "case_count": len(plan.case_ids),
                "repetitions": plan.repetitions,
                "planned_case_runs": request_count,
                "live_requests_may_exceed_case_runs": True,
            },
            indent=2,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
