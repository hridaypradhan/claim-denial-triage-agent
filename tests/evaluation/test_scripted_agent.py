"""Tests for reusable scripted-agent evaluation plans."""

import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from evaluation.systems import (
    SCRIPTED_AGENT,
    predict_scripted_agent,
)

from claim_agent.agent.canonical_trace import TraceEventType
from claim_agent.agent.model_interface import ModelRequest
from claim_agent.agent.scripted_model import ScriptedModelOutput
from claim_agent.contracts import Disposition, RouteOutcome
from claim_agent.tools.knowledge_store import KnowledgeStore
from evaluation.pilot_data import load_dataset
from evaluation.scripted_agent import (
    EvaluationCasePreparationError,
    ScriptedCasePlan,
    ScriptedCasePlanError,
    build_non_agent_output_package,
    execute_scripted_agent_case,
    prepare_evaluation_case,
    scripted_plan_for_case,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SET_A_MANIFEST = REPO_ROOT / "datasets" / "development" / "set_a" / "manifest.json"
AS_OF = date(2026, 9, 6)
KNOWLEDGE_ROOT = REPO_ROOT / "knowledge"


class ScriptedCasePlanTests(unittest.TestCase):
    def test_each_run_receives_a_fresh_model_client(self) -> None:
        """One evaluation run must not consume another run's responses."""

        plan = ScriptedCasePlan(
            system_instructions="Use only evidence supplied by approved tools.",
            model_outputs=(
                ScriptedModelOutput(
                    response_text='{"action": "request_manual_review", '
                    '"reason": "Evidence is incomplete."}'
                ),
            ),
        )

        first_client = plan.create_model_client()
        second_client = plan.create_model_client()

        first_client.generate_response(
            ModelRequest(
                request_id="run-1:model:1",
                system_instructions=plan.system_instructions,
                task_context="Evaluate the first run of the claim.",
            )
        )

        self.assertEqual(first_client.remaining_output_count, 0)
        self.assertEqual(second_client.remaining_output_count, 1)

    def test_missing_case_reports_its_identifier(self) -> None:
        """A missing script should fail before an incomplete experiment runs."""

        with self.assertRaisesRegex(
            ScriptedCasePlanError,
            "CLM-2026-610102-L1",
        ):
            scripted_plan_for_case(
                case_id="CLM-2026-610102-L1",
                plans_by_case_id={},
            )

    def test_empty_model_output_sequence_is_rejected(self) -> None:
        """Every agent-routed case needs at least one scripted response."""

        with self.assertRaisesRegex(
            ScriptedCasePlanError,
            "at least one scripted response",
        ):
            ScriptedCasePlan(
                system_instructions="Use only retrieved evidence.",
                model_outputs=(),
            )


class EvaluationCasePreparationTests(unittest.TestCase):
    def test_every_dataset_a_case_prepares_one_matching_route(self) -> None:
        """The shared adapter should prepare every existing Dataset A case."""

        dataset = load_dataset(SET_A_MANIFEST)

        for case in dataset.cases:
            with self.subTest(case=case.case_id):
                prepared_case = prepare_evaluation_case(case, AS_OF)

                self.assertEqual(
                    prepared_case.baseline_finding.claim_id,
                    case.claim_id,
                )
                self.assertEqual(
                    prepared_case.baseline_finding.service_line_id,
                    case.service_line_id,
                )
                self.assertEqual(
                    prepared_case.route_decision.claim_id,
                    case.claim_id,
                )

                if prepared_case.requires_agent:
                    self.assertIsNotNone(prepared_case.claim_context)
                else:
                    self.assertIsNone(prepared_case.claim_context)

    def test_non_agent_dataset_a_cases_produce_output_packages(self) -> None:
        """Rule-resolved cases should be packaged without model execution."""

        dataset = load_dataset(SET_A_MANIFEST)
        packaged_case_count = 0

        for case in dataset.cases:
            prepared_case = prepare_evaluation_case(case, AS_OF)

            if prepared_case.requires_agent:
                continue

            with self.subTest(case=case.case_id):
                output_package = build_non_agent_output_package(
                    prepared_case=prepared_case,
                    run_id=f"evaluation-{case.case_id}",
                    package_version="v1",
                    knowledge_snapshot_id="knowledge-v0",
                )

                self.assertEqual(output_package.claim_id, case.claim_id)
                self.assertEqual(
                    output_package.service_line_id,
                    case.service_line_id,
                )
                self.assertEqual(
                    output_package.route_outcome,
                    prepared_case.route_decision.outcome,
                )
                self.assertIsNone(output_package.proposed_decision)
                self.assertIsNone(output_package.validation_result)

                if (
                    prepared_case.route_decision.outcome
                    is RouteOutcome.DETERMINISTIC_ONLY
                ):
                    self.assertEqual(
                        output_package.final_disposition,
                        prepared_case.baseline_finding.baseline_disposition,
                    )
                else:
                    self.assertEqual(
                        output_package.final_disposition,
                        Disposition.MANUAL_REVIEW,
                    )

                packaged_case_count += 1

        self.assertGreater(packaged_case_count, 0)

    def test_agent_case_cannot_use_non_agent_package_path(self) -> None:
        """Agent-authorized work must not bypass its runtime and validator."""

        dataset = load_dataset(SET_A_MANIFEST)
        prepared_agent_case = next(
            prepared_case
            for case in dataset.cases
            if (prepared_case := prepare_evaluation_case(case, AS_OF)).requires_agent
        )

        with self.assertRaisesRegex(
            EvaluationCasePreparationError,
            "requires agent execution",
        ):
            build_non_agent_output_package(
                prepared_case=prepared_agent_case,
                run_id="invalid-shortcut",
                package_version="v1",
                knowledge_snapshot_id="knowledge-v0",
            )

    def test_claim_identity_mismatch_is_rejected_before_execution(self) -> None:
        """A manifest must not silently point at another claim's file."""

        dataset = load_dataset(SET_A_MANIFEST)
        valid_case = dataset.cases[0]
        mismatched_case = replace(
            valid_case,
            claim_id="CLM-WRONG",
            case_id=f"CLM-WRONG-L{valid_case.service_line_id}",
        )

        with self.assertRaisesRegex(
            EvaluationCasePreparationError,
            "but the file contains",
        ):
            prepare_evaluation_case(mismatched_case, AS_OF)


class ScriptedAgentExecutionTests(unittest.TestCase):
    def test_agent_case_produces_package_and_complete_trace(self) -> None:
        """An agent-routed case should stop safely with auditable artifacts."""

        dataset = load_dataset(SET_A_MANIFEST)
        prepared_case = next(
            prepared_case
            for case in dataset.cases
            if (prepared_case := prepare_evaluation_case(case, AS_OF)).requires_agent
        )
        plan = ScriptedCasePlan(
            system_instructions=(
                "Use only approved evidence. Request human review when "
                "the available evidence cannot support a decision."
            ),
            model_outputs=(
                ScriptedModelOutput(
                    response_text=(
                        '{"action": "request_manual_review", '
                        '"reason": "Required policy evidence is unavailable."}'
                    )
                ),
            ),
        )

        execution = execute_scripted_agent_case(
            prepared_case=prepared_case,
            plan=plan,
            knowledge_store=KnowledgeStore(KNOWLEDGE_ROOT),
            run_id=f"evaluation-{prepared_case.case.case_id}",
            as_of=AS_OF,
        )

        self.assertEqual(
            execution.output_package.final_disposition,
            Disposition.MANUAL_REVIEW,
        )
        self.assertEqual(
            execution.output_package.claim_id,
            prepared_case.case.claim_id,
        )
        self.assertEqual(
            execution.canonical_trace_events[0].event_type,
            TraceEventType.RUN_STARTED,
        )
        self.assertEqual(
            execution.canonical_trace_events[-1].event_type,
            TraceEventType.MANUAL_REVIEW,
        )
        self.assertEqual(
            {event.run_id for event in execution.canonical_trace_events},
            {f"evaluation-{prepared_case.case.case_id}"},
        )

    def test_scripted_agent_predicts_every_dataset_a_case(self) -> None:
        """S0 should package all cases and safely escalate its agent cases."""

        dataset = load_dataset(SET_A_MANIFEST)
        predictions = {
            case.case_id: predict_scripted_agent(case, AS_OF) for case in dataset.cases
        }

        self.assertEqual(len(predictions), len(dataset.cases))
        self.assertTrue(
            all(
                prediction.system == SCRIPTED_AGENT
                for prediction in predictions.values()
            )
        )
        self.assertTrue(
            all(
                prediction.output_package is not None
                for prediction in predictions.values()
            )
        )

        traced_predictions = {
            case_id: prediction
            for case_id, prediction in predictions.items()
            if prediction.trace_events
        }
        self.assertEqual(
            set(traced_predictions),
            {
                "CLM-2026-610102-L1",
                "CLM-2026-610110-L1",
            },
        )
        self.assertTrue(
            all(
                prediction.disposition == "MANUAL_REVIEW"
                for prediction in traced_predictions.values()
            )
        )


if __name__ == "__main__":
    unittest.main()
