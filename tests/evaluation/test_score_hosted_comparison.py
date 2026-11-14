"""Offline tests for hosted comparison scoring."""

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
from claim_agent.contracts import (
    AgentDecision,
    AgentStatus,
    BaselineFinding,
    CriterionAssessment,
    CriterionStatus,
    Disposition,
    OutputPackage,
    RootCause,
    RouteOutcome,
    ValidationOutcome,
    ValidationResult,
)
from evaluation.hosted_comparison import (
    load_hosted_comparison_plan,
)
from evaluation.pilot_data import CriterionLabel, Label
from evaluation.run_hosted_comparison import (
    run_selected_comparisons,
    select_comparison_runs,
)
from evaluation.score_hosted_comparison import (
    HostedComparisonScoringError,
    _criterion_evidence_support,
    rank_candidates,
    score_comparison,
)


class ManualReviewTransport:
    """Return deterministic offline model output."""

    def generate(
        self,
        request: HostedModelTransportRequest,
    ) -> HostedModelTransportResponse:
        """Return one valid action with usage data."""

        return HostedModelTransportResponse(
            response_text=json.dumps(
                {
                    "action": "request_manual_review",
                    "reason": "Offline scorer test.",
                }
            ),
            provider_request_id="offline-score-request",
            input_token_count=40,
            output_token_count=8,
        )


def offline_client_factory(profile):
    """Create an offline hosted client."""

    return HostedModelClient(
        transport=ManualReviewTransport(),
        model_name=profile.model_id,
        timeout_seconds=profile.request_timeout_seconds,
    )


class HostedComparisonScoringTests(unittest.TestCase):
    """Verify partial reports and complete-only winner selection."""

    def test_partial_report_never_names_a_winner(self) -> None:
        """Incomplete evidence must not select a production model."""

        plan = load_hosted_comparison_plan()
        selection = select_comparison_runs(
            plan=plan,
            model_id=plan.candidates[0].model_id,
            case_id=plan.case_ids[0],
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory)
            run_selected_comparisons(
                plan=plan,
                output_root=output_root,
                code_commit="offline-test-commit",
                selection=selection,
                confirm_live=True,
                client_factory=offline_client_factory,
            )
            report = score_comparison(
                plan=plan,
                output_root=output_root,
                allow_partial=True,
            )

        self.assertFalse(report["complete"])
        self.assertIsNone(report["winner"])
        self.assertEqual(3, report["completed_runs"])
        self.assertEqual(24, report["planned_runs"])
        self.assertEqual(
            1.0,
            report["scores"][plan.candidates[0].model_id][
                "stability_rate"
            ]["value"],
        )

    def test_safety_outranks_all_later_metrics(self) -> None:
        """A faster unsafe model must never outrank a safe model."""

        safe_metrics = self._ranking_metrics(
            critical_safety_failures=0,
            rate=0.5,
            elapsed_ms=1000.0,
        )
        unsafe_metrics = self._ranking_metrics(
            critical_safety_failures=1,
            rate=1.0,
            elapsed_ms=1.0,
        )

        self.assertEqual(
            ["safe-model", "unsafe-model"],
            rank_candidates(
                {
                    "unsafe-model": unsafe_metrics,
                    "safe-model": safe_metrics,
                }
            ),
        )

    def test_criterion_evidence_must_support_matching_criterion(
        self,
    ) -> None:
        """A retrieved citation is not automatically relevant evidence."""

        decision = AgentDecision(
            decision_id="decision-1",
            claim_id="CLM-TEST",
            service_line_id=1,
            root_cause=RootCause.MEDICAL_NECESSITY,
            recommended_disposition=(
                Disposition.APPEAL_WITH_DOCUMENTATION
            ),
            criterion_assessments=[
                CriterionAssessment(
                    criterion_id="A",
                    status=CriterionStatus.SUPPORTED,
                    explanation="The wrong passage was cited.",
                    citation_ids=["ENC-WRONG@v1#L1"],
                )
            ],
            agent_status=AgentStatus.COMPLETED,
            rationale="Test decision.",
        )
        package = OutputPackage(
            run_id="run-1",
            package_version="v1",
            knowledge_snapshot_id="knowledge-v1",
            claim_id="CLM-TEST",
            service_line_id=1,
            route_outcome=RouteOutcome.AGENT_REQUIRED,
            baseline_finding=BaselineFinding(
                claim_id="CLM-TEST",
                service_line_id=1,
                denial_codes=["CO-50"],
                root_cause=RootCause.MEDICAL_NECESSITY,
                baseline_disposition=(
                    Disposition.APPEAL_WITH_DOCUMENTATION
                ),
                rationale="Test baseline.",
                deadline_status="ACTIVE",
            ),
            proposed_decision=decision,
            validation_result=ValidationResult(
                decision_id="decision-1",
                claim_id="CLM-TEST",
                service_line_id=1,
                outcome=ValidationOutcome.ACCEPTED,
                validated_decision=decision,
            ),
            final_disposition=(
                Disposition.APPEAL_WITH_DOCUMENTATION
            ),
            final_root_cause=RootCause.MEDICAL_NECESSITY,
        )
        label = Label(
            case_id="fixture-case",
            root_cause="MEDICAL_NECESSITY",
            disposition="APPEAL_WITH_DOCUMENTATION",
            requires_manual_review=False,
            manual_review_reasons=(),
            deadline=None,
            forbidden_outcomes=(),
            criteria=(
                CriterionLabel(
                    criterion_id="A",
                    status="SUPPORTED",
                    passage_ids=("ENC-RIGHT@v1#L1",),
                ),
            ),
        )

        self.assertEqual(
            (0, 1),
            _criterion_evidence_support(package, label),
        )

    def test_incomplete_comparison_is_rejected_by_default(
        self,
    ) -> None:
        """Final scoring should require every planned artifact."""

        plan = load_hosted_comparison_plan()

        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(
                HostedComparisonScoringError,
                "Comparison is incomplete",
            ):
                score_comparison(
                    plan=plan,
                    output_root=Path(temporary_directory),
                    allow_partial=False,
                )

    @staticmethod
    def _ranking_metrics(
        *,
        critical_safety_failures: int,
        rate: float,
        elapsed_ms: float,
    ) -> dict[str, object]:
        """Create the fields consumed by the ranking function."""

        rate_value = {
            "hits": 1,
            "total": 1,
            "value": rate,
        }
        return {
            "critical_safety_failures": (
                critical_safety_failures
            ),
            "valid_action_schema_rate": rate_value,
            "correct_policy_request_rate": rate_value,
            "joint_accuracy": rate_value,
            "criterion_evidence_support_rate": rate_value,
            "citation_precision": rate_value,
            "correct_manual_review_rate": rate_value,
            "stability_rate": rate_value,
            "provider_failures": 0,
            "mean_model_elapsed_ms": elapsed_ms,
            "input_tokens": 1,
            "output_tokens": 1,
        }


if __name__ == "__main__":
    unittest.main()
