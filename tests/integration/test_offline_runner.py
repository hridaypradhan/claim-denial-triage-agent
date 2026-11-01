"""Exercise the complete offline runtime through its command-line runner."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

from claim_agent.agent.offline_runner import (
    POLICY_SOURCE_ID,
    main,
    run_scripted_demo,
)


class OfflineRunnerIntegrationTests(unittest.TestCase):
    """Verify the scripted boundaries work together as one complete flow."""

    def test_scripted_demo_completes_with_grounded_appeal(self) -> None:
        """The demo should retrieve evidence and accept a cited appeal."""

        summary = run_scripted_demo()

        self.assertEqual(summary["phase"], "COMPLETED")
        self.assertEqual(
            summary["terminal_reason"],
            "decision_accepted",
        )
        self.assertEqual(
            summary["validated_decision"]["recommended_disposition"],
            "APPEAL",
        )
        self.assertEqual(
            summary["validated_decision"]["citation_ids"],
            [POLICY_SOURCE_ID],
        )
        self.assertEqual(
            summary["budget_usage"],
            {
                "model_calls": 2,
                "tool_calls": 1,
                "repair_attempts": 0,
                "total_steps": 3,
            },
        )
        self.assertIn(
            "TOOL_RETURNED",
            summary["trace_events"],
        )
        self.assertIn(
            "VALIDATION_RETURNED",
            summary["trace_events"],
        )
        self.assertEqual(
            summary["trace_events"][-1],
            "RUN_COMPLETED",
        )

    def test_main_prints_machine_readable_json(self) -> None:
        """Automation should be able to parse the default CLI output."""

        captured_output = io.StringIO()

        with redirect_stdout(captured_output):
            exit_code = main([])

        printed_summary = json.loads(captured_output.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            printed_summary["run_id"],
            "offline-demo-medical-necessity",
        )
        self.assertEqual(
            printed_summary["phase"],
            "COMPLETED",
        )


if __name__ == "__main__":
    unittest.main()
