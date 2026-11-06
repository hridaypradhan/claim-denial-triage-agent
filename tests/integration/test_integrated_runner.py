"""Exercise the production-shaped workflow through its CLI runner."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

from claim_agent.agent.integrated_runner import (
    DEMO_RUN_ID,
    ENCOUNTER_PASSAGE_IDS,
    POLICY_PASSAGE_IDS,
    main,
    run_integrated_demo,
)


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
        self.assertEqual(
            "RUN_COMPLETED",
            summary["trace_events"][-1],
        )

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


if __name__ == "__main__":
    unittest.main()
