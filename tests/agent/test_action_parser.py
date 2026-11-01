"""Tests for converting raw model text into permitted runtime actions."""

from __future__ import annotations

import unittest

from claim_agent.agent.action_parser import (
    ModelOutputParseError,
    RuntimeActionType,
    parse_model_action,
)


class ModelActionParserTests(unittest.TestCase):
    """Verify accepted action shapes and rejected untrusted output."""

    def test_parses_valid_tool_action(self) -> None:
        """A valid lookup request should expose its tool name and arguments."""

        action = parse_model_action("""
            {
                "action": "call_tool",
                "tool_name": "retrieve_medical_policy",
                "tool_arguments": {
                    "payer": "BCBS_AZ",
                    "procedure_code": "70553"
                }
            }
            """)

        self.assertEqual(action.action_type, RuntimeActionType.CALL_TOOL)
        self.assertEqual(action.tool_name, "retrieve_medical_policy")
        self.assertEqual(action.tool_arguments["payer"], "BCBS_AZ")
        self.assertEqual(action.tool_arguments["procedure_code"], "70553")

    def test_parses_valid_proposed_decision(self) -> None:
        """Decision contents remain opaque until the shared contract exists."""

        action = parse_model_action("""
            {
                "action": "propose_decision",
                "decision": {
                    "recommended_disposition": "MANUAL_REVIEW"
                }
            }
            """)

        self.assertEqual(
            action.action_type,
            RuntimeActionType.PROPOSE_DECISION,
        )
        self.assertEqual(
            action.decision_payload["recommended_disposition"],
            "MANUAL_REVIEW",
        )

    def test_parses_valid_manual_review_action(self) -> None:
        """The model may stop when required evidence remains unavailable."""

        action = parse_model_action("""
            {
                "action": "request_manual_review",
                "reason": "required encounter note is unavailable"
            }
            """)

        self.assertEqual(
            action.action_type,
            RuntimeActionType.REQUEST_MANUAL_REVIEW,
        )
        self.assertEqual(
            action.manual_review_reason,
            "required encounter note is unavailable",
        )

    def test_rejects_invalid_json(self) -> None:
        """Malformed structured output should enter the repair path."""

        with self.assertRaisesRegex(
            ModelOutputParseError,
            "valid JSON",
        ):
            parse_model_action('{"action": "call_tool"')

    def test_rejects_json_value_that_is_not_an_object(self) -> None:
        """A list cannot describe one named runtime action."""

        with self.assertRaisesRegex(
            ModelOutputParseError,
            "JSON object",
        ):
            parse_model_action('["call_tool"]')

    def test_rejects_missing_action_name(self) -> None:
        """The runtime cannot infer what an incomplete response intended."""

        with self.assertRaisesRegex(
            ModelOutputParseError,
            "action",
        ):
            parse_model_action('{"tool_name": "retrieve_medical_policy"}')

    def test_rejects_unsupported_action(self) -> None:
        """The model cannot invent an external action such as submission."""

        with self.assertRaisesRegex(
            ModelOutputParseError,
            "Unsupported action",
        ):
            parse_model_action('{"action": "submit_appeal_to_payer"}')

    def test_rejects_tool_action_without_tool_name(self) -> None:
        """A tool request must identify the allowlisted tool it wants."""

        with self.assertRaisesRegex(
            ModelOutputParseError,
            "tool_name",
        ):
            parse_model_action("""
                {
                    "action": "call_tool",
                    "tool_arguments": {}
                }
                """)

    def test_rejects_tool_arguments_that_are_not_an_object(self) -> None:
        """Tool arguments must use named fields rather than free-form text."""

        with self.assertRaisesRegex(
            ModelOutputParseError,
            "tool_arguments",
        ):
            parse_model_action("""
                {
                    "action": "call_tool",
                    "tool_name": "retrieve_medical_policy",
                    "tool_arguments": "Find the MRI policy"
                }
                """)

    def test_rejects_blank_manual_review_reason(self) -> None:
        """A manual-review result must explain why human attention is needed."""

        with self.assertRaisesRegex(
            ModelOutputParseError,
            "reason",
        ):
            parse_model_action("""
                {
                    "action": "request_manual_review",
                    "reason": "   "
                }
                """)


if __name__ == "__main__":
    unittest.main()
