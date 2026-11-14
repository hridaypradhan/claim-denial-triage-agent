"""Offline tests for the resumable hosted comparison runner."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from claim_agent.agent.hosted.client import (
    HostedModelClient,
    HostedModelTransportRequest,
    HostedModelTransportResponse,
)
from evaluation.hosted_comparison import (
    load_hosted_comparison_plan,
)
from evaluation.run_hosted_comparison import (
    HostedComparisonExecutionError,
    comparison_run_directory,
    run_selected_comparisons,
    select_comparison_runs,
)


class ManualReviewTransport:
    """Provide a valid response without network access."""

    def generate(
        self,
        request: HostedModelTransportRequest,
    ) -> HostedModelTransportResponse:
        """Return one bounded manual-review action."""

        return HostedModelTransportResponse(
            response_text=json.dumps(
                {
                    "action": "request_manual_review",
                    "reason": "Offline comparison test.",
                }
            ),
            provider_request_id="offline-runner-request",
            input_token_count=50,
            output_token_count=10,
        )


def offline_client_factory(*, profile):
    """Create a client using the injected offline transport."""

    return HostedModelClient(
        transport=ManualReviewTransport(),
        model_name=profile.model_id,
        timeout_seconds=profile.request_timeout_seconds,
    )


class HostedComparisonRunnerTests(unittest.TestCase):
    """Verify filtering, confirmation, persistence, and resume behavior."""

    def test_selection_can_limit_to_one_run(self) -> None:
        """Filters should support a cautious one-run smoke test."""

        plan = load_hosted_comparison_plan()
        selection = select_comparison_runs(
            plan=plan,
            model_id=plan.candidates[0].model_id,
            case_id=plan.case_ids[0],
            repeat_index=1,
        )

        self.assertEqual(1, selection.run_count)

    def test_live_confirmation_is_required_before_client_creation(
        self,
    ) -> None:
        """Missing confirmation must stop before any provider boundary."""

        plan = load_hosted_comparison_plan()
        selection = select_comparison_runs(
            plan=plan,
            model_id=plan.candidates[0].model_id,
            case_id=plan.case_ids[0],
            repeat_index=1,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(
                HostedComparisonExecutionError,
                "explicit confirmation",
            ):
                run_selected_comparisons(
                    plan=plan,
                    output_root=Path(temporary_directory),
                    code_commit="offline-test-commit",
                    selection=selection,
                    confirm_live=False,
                    client_factory=offline_client_factory,
                )

    def test_completed_run_is_persisted_and_then_skipped(
        self,
    ) -> None:
        """Resume should not repeat a completed provider run."""

        plan = load_hosted_comparison_plan()
        model_id = plan.candidates[0].model_id
        case_id = plan.case_ids[0]
        selection = select_comparison_runs(
            plan=plan,
            model_id=model_id,
            case_id=case_id,
            repeat_index=1,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory)
            first_summary = run_selected_comparisons(
                plan=plan,
                output_root=output_root,
                code_commit="offline-test-commit",
                selection=selection,
                confirm_live=True,
                client_factory=offline_client_factory,
            )
            second_summary = run_selected_comparisons(
                plan=plan,
                output_root=output_root,
                code_commit="offline-test-commit",
                selection=selection,
                confirm_live=True,
                client_factory=offline_client_factory,
            )
            run_directory = comparison_run_directory(
                output_root=output_root,
                plan_version=plan.plan_version,
                model_id=model_id,
                case_id=case_id,
                repeat_index=1,
            )

            self.assertTrue(
                (run_directory / "output-package.json").is_file()
            )
            self.assertTrue(
                (run_directory / "complete.json").is_file()
            )
            self.assertEqual(
                1,
                len(list((run_directory / "trace").glob("*.jsonl"))),
            )

        self.assertEqual(1, first_summary["completed_runs"])
        self.assertEqual(0, first_summary["skipped_completed_runs"])
        self.assertEqual(0, second_summary["completed_runs"])
        self.assertEqual(1, second_summary["skipped_completed_runs"])

    def test_partial_directory_is_never_overwritten(self) -> None:
        """Interrupted artifacts must be reviewed before retrying."""

        plan = load_hosted_comparison_plan()
        model_id = plan.candidates[0].model_id
        case_id = plan.case_ids[0]
        selection = select_comparison_runs(
            plan=plan,
            model_id=model_id,
            case_id=case_id,
            repeat_index=1,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory)
            run_directory = comparison_run_directory(
                output_root=output_root,
                plan_version=plan.plan_version,
                model_id=model_id,
                case_id=case_id,
                repeat_index=1,
            )
            run_directory.mkdir(parents=True)

            with self.assertRaisesRegex(
                HostedComparisonExecutionError,
                "Incomplete run directory",
            ):
                run_selected_comparisons(
                    plan=plan,
                    output_root=output_root,
                    code_commit="offline-test-commit",
                    selection=selection,
                    confirm_live=True,
                    client_factory=offline_client_factory,
                )


if __name__ == "__main__":
    unittest.main()
