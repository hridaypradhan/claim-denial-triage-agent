"""Offline tests for one hosted comparison case execution."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from claim_agent.agent.canonical_trace import TraceEventType
from claim_agent.agent.hosted.client import (
    HostedModelClient,
    HostedModelTransportRequest,
    HostedModelTransportResponse,
)
from claim_agent.tools.knowledge_store import KnowledgeStore
from evaluation.hosted_comparison import (
    load_hosted_comparison_plan,
)
from evaluation.hosted_execution import execute_hosted_case
from evaluation.pilot_data import load_dataset
from evaluation.scripted_agent import prepare_evaluation_case

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_ROOT = REPO_ROOT / "knowledge"


class ManualReviewTransport:
    """Return a valid safe action without contacting Voyager."""

    def generate(
        self,
        request: HostedModelTransportRequest,
    ) -> HostedModelTransportResponse:
        """Return one bounded manual-review action."""

        return HostedModelTransportResponse(
            response_text=json.dumps(
                {
                    "action": "request_manual_review",
                    "reason": (
                        "Offline execution does not interpret the "
                        "retrieved clinical evidence."
                    ),
                }
            ),
            provider_request_id="offline-comparison-request",
            input_token_count=80,
            output_token_count=18,
        )


class HostedCaseExecutionTests(unittest.TestCase):
    """Verify one comparison run through production boundaries."""

    def test_case_produces_package_and_complete_hosted_trace(
        self,
    ) -> None:
        """An injected hosted client should require no key or network."""

        plan = load_hosted_comparison_plan()
        dataset = load_dataset(plan.manifest_path())
        selected_case = next(
            case
            for case in dataset.cases
            if case.case_id == plan.case_ids[0]
        )
        prepared_case = prepare_evaluation_case(
            selected_case,
            date.fromisoformat(dataset.as_of),
        )
        profile = plan.candidates[0].hosted_profile()
        model_client = HostedModelClient(
            transport=ManualReviewTransport(),
            model_name=profile.model_id,
            timeout_seconds=profile.request_timeout_seconds,
        )

        execution = execute_hosted_case(
            prepared_case=prepared_case,
            model_client=model_client,
            profile=profile,
            budgets=plan.budgets,
            knowledge_store=KnowledgeStore(KNOWLEDGE_ROOT),
            run_id="hosted-comparison-offline-run",
            as_of=date.fromisoformat(dataset.as_of),
            code_commit="offline-test-commit",
            plan_version=plan.plan_version,
            repeat_index=1,
        )

        self.assertEqual(
            "MANUAL_REVIEW",
            execution.output_package.final_disposition.value,
        )
        self.assertEqual(
            TraceEventType.RUN_STARTED,
            execution.canonical_trace_events[0].event_type,
        )
        self.assertEqual(
            TraceEventType.MANUAL_REVIEW,
            execution.canonical_trace_events[-1].event_type,
        )

        run_config = execution.canonical_trace_events[0].payload["config"]
        self.assertEqual("0.3", run_config["config_version"])
        self.assertEqual(
            profile.model_id,
            run_config["model_id"],
        )
        self.assertEqual(
            "planning-v1",
            run_config["prompt_version"],
        )

        evaluation_context = (
            execution.canonical_trace_events[0].payload[
                "evaluation_context"
            ]
        )
        self.assertEqual(
            1,
            evaluation_context["repeat_index"],
        )
        self.assertEqual(
            plan.plan_version,
            evaluation_context["experiment_id"],
        )

    def test_non_agent_case_is_rejected(self) -> None:
        """The hosted executor must not bypass deterministic routing."""

        plan = load_hosted_comparison_plan()
        dataset = load_dataset(plan.manifest_path())
        prepared_cases = [
            prepare_evaluation_case(
                case,
                date.fromisoformat(dataset.as_of),
            )
            for case in dataset.cases
        ]
        non_agent_case = next(
            prepared_case
            for prepared_case in prepared_cases
            if not prepared_case.requires_agent
        )
        profile = plan.candidates[0].hosted_profile()
        model_client = HostedModelClient(
            transport=ManualReviewTransport(),
            model_name=profile.model_id,
            timeout_seconds=profile.request_timeout_seconds,
        )

        with self.assertRaisesRegex(
            ValueError,
            "does not require hosted agent execution",
        ):
            execute_hosted_case(
                prepared_case=non_agent_case,
                model_client=model_client,
                profile=profile,
                budgets=plan.budgets,
                knowledge_store=KnowledgeStore(KNOWLEDGE_ROOT),
                run_id="invalid-hosted-run",
                as_of=date.fromisoformat(dataset.as_of),
                code_commit="offline-test-commit",
                plan_version=plan.plan_version,
                repeat_index=1,
            )


if __name__ == "__main__":
    unittest.main()
