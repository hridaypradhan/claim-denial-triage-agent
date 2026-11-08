"""Exercise the production-shaped workflow through its CLI runner."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from claim_agent.agent.integrated_runner import (
    DEMO_RUN_ID,
    ENCOUNTER_PASSAGE_IDS,
    KNOWLEDGE_ROOT,
    POLICY_PASSAGE_IDS,
    main,
    run_integrated_demo,
)
from claim_agent.tools.knowledge_store import KnowledgeStore


class IntegratedRunnerTests(unittest.TestCase):
    """Verify the integrated demo and its command-line output."""

    def test_demo_completes_with_real_tools_and_validation(self) -> None:
        """The demo should retrieve evidence and accept a grounded decision."""

        summary = run_integrated_demo()

        self.assertEqual("COMPLETED", summary["phase"])
        self.assertEqual(
            "decision_accepted",
            summary["terminal_reason"],
        )
        self.assertEqual(
            {
                "model_calls": 3,
                "tool_calls": 2,
                "repair_attempts": 0,
                "total_steps": 5,
            },
            summary["budget_usage"],
        )
        self.assertEqual(
            "A AND B AND (C OR D)",
            summary["policy_formula"],
        )

        retrieved_passage_ids = set(summary["retrieved_passage_ids"])
        required_passage_ids = {
            *POLICY_PASSAGE_IDS,
            *ENCOUNTER_PASSAGE_IDS,
        }

        self.assertTrue(required_passage_ids.issubset(retrieved_passage_ids))
        self.assertEqual(
            sorted(required_passage_ids),
            sorted(summary["validated_decision"]["citation_ids"]),
        )

        output_package = summary["output_package"]

        expected_snapshot_id = KnowledgeStore(KNOWLEDGE_ROOT).snapshot_id

        self.assertEqual(
            expected_snapshot_id,
            output_package["knowledge_snapshot_id"],
        )

        self.assertEqual(
            DEMO_RUN_ID,
            output_package["run_id"],
        )
        self.assertEqual(
            "APPEAL_WITH_DOCUMENTATION",
            output_package["final_disposition"],
        )
        self.assertEqual(
            "PENDING_REVIEW",
            output_package["validation_result"]["approval_state"],
        )
        self.assertEqual(
            "RUN_COMPLETED",
            summary["trace_events"][-1],
        )

    def test_demo_deadline_comes_from_baseline_calculation(self) -> None:
        """The package should carry the deadline the baseline calculates.

        BCBS_AZ allows 180 days to appeal from the 2026-07-11 remittance.
        """

        finding = run_integrated_demo()["output_package"]["baseline_finding"]

        self.assertEqual("2027-01-07", finding["filing_deadline"])
        self.assertEqual("ACTIVE", finding["deadline_status"])

    def test_main_prints_machine_readable_json(self) -> None:
        """Automation should be able to parse the default CLI output."""

        captured_output = io.StringIO()

        with redirect_stdout(captured_output):
            exit_code = main([])

        printed_summary = json.loads(captured_output.getvalue())

        self.assertEqual(0, exit_code)
        self.assertEqual(
            DEMO_RUN_ID,
            printed_summary["run_id"],
        )
        self.assertEqual(
            "COMPLETED",
            printed_summary["phase"],
        )
        self.assertEqual(
            "decision_accepted",
            printed_summary["terminal_reason"],
        )

    def test_cli_writes_complete_canonical_trace_when_requested(self) -> None:
        """The CLI should create one immutable evaluation-ready artifact."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            trace_directory = Path(temporary_directory)
            captured_output = io.StringIO()

            with redirect_stdout(captured_output):
                exit_code = main(
                    [
                        "--trace-directory",
                        str(trace_directory),
                        "--code-commit",
                        "test-commit-123",
                    ]
                )

            printed_summary = json.loads(captured_output.getvalue())
            trace_path = trace_directory / f"{DEMO_RUN_ID}.jsonl"
            trace_events = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(0, exit_code)
            self.assertEqual(str(trace_path), printed_summary["canonical_trace_path"])
            self.assertEqual("RUN_STARTED", trace_events[0]["event_type"])
            self.assertEqual("RUN_COMPLETED", trace_events[-1]["event_type"])
            self.assertEqual(
                "test-commit-123",
                trace_events[0]["payload"]["config"]["code_commit"],
            )
            self.assertNotIn("routing_reason", trace_events[0]["payload"]["route"])


if __name__ == "__main__":
    unittest.main()
