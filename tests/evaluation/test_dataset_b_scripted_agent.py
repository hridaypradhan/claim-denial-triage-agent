"""End-to-end checks for Dataset B scripted-agent evaluation."""

import unittest
from datetime import date
from pathlib import Path

from claim_agent.agent.canonical_trace import TraceEventType
from claim_agent.contracts import RouteOutcome
from evaluation.dataset_b_scripts import DATASET_B_SCRIPTED_PLANS
from evaluation.pilot_data import load_dataset, load_labels
from evaluation.scripted_agent import prepare_evaluation_case
from evaluation.systems import SCRIPTED_AGENT, predict_scripted_agent

REPO_ROOT = Path(__file__).resolve().parents[2]
SET_B_MANIFEST = REPO_ROOT / "datasets" / "development" / "set_b" / "manifest.json"
SET_B_LABELS = REPO_ROOT / "datasets" / "labels" / "set_b_development.adjudicated.json"


class DatasetBScriptedAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """Run each case once and reuse its deterministic artifacts."""

        cls.dataset = load_dataset(SET_B_MANIFEST)
        cls.labels = load_labels(SET_B_LABELS, cls.dataset)
        cls.as_of = date.fromisoformat(cls.dataset.as_of)
        cls.prepared_cases = {
            case.case_id: prepare_evaluation_case(case, cls.as_of)
            for case in cls.dataset.cases
        }
        cls.predictions = {
            case.case_id: predict_scripted_agent(case, cls.as_of)
            for case in cls.dataset.cases
        }

    def test_plan_registry_matches_agent_routed_cases(self) -> None:
        """Every agent-routed case, and only those cases, needs a script."""

        agent_case_ids = {
            case_id
            for case_id, prepared_case in self.prepared_cases.items()
            if prepared_case.route_decision.outcome is RouteOutcome.AGENT_REQUIRED
        }

        self.assertEqual(
            set(DATASET_B_SCRIPTED_PLANS),
            agent_case_ids,
        )
        self.assertNotIn(
            "CLM-2026-620112-L1",
            DATASET_B_SCRIPTED_PLANS,
        )

    def test_predictions_match_adjudicated_dataset_b_labels(self) -> None:
        """S0 should produce the adjudicated outcome for all twelve cases."""

        self.assertEqual(len(self.predictions), 12)

        for case_id, label in self.labels.items():
            with self.subTest(case=case_id):
                prediction = self.predictions[case_id]

                self.assertEqual(prediction.system, SCRIPTED_AGENT)
                self.assertEqual(
                    prediction.root_cause,
                    label.root_cause,
                )
                self.assertEqual(
                    prediction.disposition,
                    label.disposition,
                )
                self.assertEqual(
                    prediction.deadline,
                    label.deadline,
                )
                self.assertIsNotNone(prediction.output_package)

    def test_only_agent_routed_cases_have_runtime_traces(self) -> None:
        """The router-only B12 case must not pretend that a model ran."""

        traced_case_ids = {
            case_id
            for case_id, prediction in self.predictions.items()
            if prediction.trace_events
        }

        self.assertEqual(
            traced_case_ids,
            set(DATASET_B_SCRIPTED_PLANS),
        )
        self.assertFalse(self.predictions["CLM-2026-620112-L1"].trace_events)

    def test_multi_document_cases_use_three_bounded_tool_calls(self) -> None:
        """B03 and B09 may retrieve policy plus two encounter documents."""

        for case_id in (
            "CLM-2026-620103-L1",
            "CLM-2026-620109-L1",
        ):
            with self.subTest(case=case_id):
                tool_requests = [
                    event
                    for event in self.predictions[case_id].trace_events
                    if event.event_type is TraceEventType.TOOL_REQUESTED
                ]
                self.assertEqual(len(tool_requests), 3)

    def test_embedded_instruction_is_not_used_as_evidence(self) -> None:
        """B10's instruction-like note text must never become a citation."""

        output_package = self.predictions["CLM-2026-620110-L1"].output_package

        self.assertIsNotNone(output_package)
        self.assertIsNotNone(output_package.proposed_decision)
        self.assertNotIn(
            "ENC-CLM-2026-620110-CN@v1#L8",
            output_package.proposed_decision.citation_ids,
        )


if __name__ == "__main__":
    unittest.main()
