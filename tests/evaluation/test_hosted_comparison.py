"""Tests for the frozen hosted-model comparison plan."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluation.hosted_comparison import (
    DEFAULT_HOSTED_COMPARISON_PLAN,
    HostedComparisonPlan,
    load_hosted_comparison_plan,
)


class HostedComparisonPlanTests(unittest.TestCase):
    """Verify the plan is frozen, development-only, and reproducible."""

    def test_runtime_budget_allows_bounded_provider_calls(
        self,
    ) -> None:
        """Total runtime must exceed the sum of request timeouts."""

        plan = load_hosted_comparison_plan()
        slowest_candidate_timeout = max(
            candidate.request_timeout_seconds for candidate in plan.candidates
        )

        self.assertGreaterEqual(
            plan.budgets.max_runtime_seconds,
            (plan.budgets.max_model_calls * slowest_candidate_timeout),
        )

    def test_default_plan_loads_without_network_access(self) -> None:
        """Plan validation must not construct a provider client."""

        plan = load_hosted_comparison_plan()

        self.assertEqual(
            "hosted-comparison-v7",
            plan.plan_version,
        )
        self.assertEqual(300.0, plan.budgets.max_runtime_seconds)
        self.assertTrue(
            all(
                candidate.request_timeout_seconds == 60.0
                for candidate in plan.candidates
            )
        )
        self.assertTrue(
            all(
                candidate.reasoning_effort == "none"
                for candidate in plan.candidates
            )
        )
        self.assertTrue(
            all(
                candidate.max_completion_tokens == 2048
                for candidate in plan.candidates
            )
        )
        self.assertEqual(3, plan.repetitions)
        self.assertEqual(8, len(plan.case_ids))
        self.assertEqual(3, len(plan.candidates))

    def test_expected_candidate_models_are_frozen(self) -> None:
        """The reviewed candidate set should not change implicitly."""

        plan = load_hosted_comparison_plan()

        self.assertEqual(
            (
                "qwen35-397b-a17b",
                "qwen36-27b",
                "beam-1-0",
            ),
            tuple(candidate.model_id for candidate in plan.candidates),
        )

    def test_every_candidate_disables_gateway_addons(self) -> None:
        """The local runtime must remain the only tool authority."""

        plan = load_hosted_comparison_plan()

        self.assertTrue(
            all(candidate.disable_gateway_addons for candidate in plan.candidates)
        )

    def test_every_candidate_uses_strict_schema_mode(self) -> None:
        """Compatibility-screened candidates should use one action schema."""

        plan = load_hosted_comparison_plan()

        self.assertTrue(
            all(
                candidate.response_format_mode == "json_schema"
                for candidate in plan.candidates
            )
        )

    def test_plan_freezes_seventy_two_case_runs(self) -> None:
        """Three models, eight cases, and three repeats equal 72 runs."""

        plan = load_hosted_comparison_plan()

        self.assertEqual(
            72,
            len(plan.candidates) * len(plan.case_ids) * plan.repetitions,
        )

    def test_held_out_manifest_is_rejected(self) -> None:
        """Model selection must never inspect held-out evaluation data."""

        raw_plan = json.loads(
            DEFAULT_HOSTED_COMPARISON_PLAN.read_text(encoding="utf-8")
        )
        raw_plan["dataset_manifest"] = "datasets/held_out/set_b/manifest.json"

        with self.assertRaisesRegex(
            ValueError,
            "development data",
        ):
            HostedComparisonPlan.model_validate(raw_plan)

    def test_unknown_case_is_rejected_against_manifest(self) -> None:
        """Every selected case must belong to the reviewed manifest."""

        raw_plan = json.loads(
            DEFAULT_HOSTED_COMPARISON_PLAN.read_text(encoding="utf-8")
        )
        raw_plan["case_ids"][0] = "CLM-UNKNOWN-L1"

        with tempfile.TemporaryDirectory() as temporary_directory:
            plan_path = Path(temporary_directory) / "comparison-plan.json"
            plan_path.write_text(
                json.dumps(raw_plan),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "missing from development manifest",
            ):
                load_hosted_comparison_plan(plan_path)


if __name__ == "__main__":
    unittest.main()
