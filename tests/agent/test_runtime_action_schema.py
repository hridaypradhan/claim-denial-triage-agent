"""Tests for the hosted runtime-action response schema."""

from __future__ import annotations

import json
import unittest

from claim_agent.agent.action_parser import RuntimeActionType, parse_model_action
from claim_agent.agent.runtime_action_schema import (
    build_runtime_action_json_schema,
    build_runtime_action_response_format,
)
from claim_agent.tools.dispatcher import ENCOUNTER_NOTE_TOOL, MEDICAL_POLICY_TOOL
from tests.agent.runtime_test_support import make_test_agent_decision_payload


ALLOWED_TOOLS = (ENCOUNTER_NOTE_TOOL, MEDICAL_POLICY_TOOL)


def action_schema_for(
    schema: dict[str, object], action_name: str
) -> dict[str, object]:
    """Return the alternative for one top-level action."""

    for action_schema in schema["anyOf"]:
        action_property = action_schema["properties"]["action"]
        if action_property["const"] == action_name:
            return action_schema
    raise AssertionError(f"No schema exists for action {action_name}.")


def tool_schema_for(
    schema: dict[str, object], tool_name: str
) -> dict[str, object]:
    """Return the alternative for one named tool."""

    for action_schema in schema["anyOf"]:
        properties = action_schema["properties"]
        tool_property = properties.get("tool_name")
        if (
            isinstance(tool_property, dict)
            and tool_property.get("const") == tool_name
        ):
            return action_schema
    raise AssertionError(f"No schema exists for tool {tool_name}.")


class RuntimeActionSchemaTests(unittest.TestCase):
    """Verify closed schemas for every permitted runtime action."""

    def test_builds_strict_chat_completions_response_format(self) -> None:
        """The provider should receive one named strict JSON schema."""

        response_format = build_runtime_action_response_format(ALLOWED_TOOLS)
        self.assertEqual("json_schema", response_format["type"])
        self.assertEqual(
            "runtime_action", response_format["json_schema"]["name"]
        )
        self.assertIs(True, response_format["json_schema"]["strict"])

        schema = response_format["json_schema"]["schema"]
        self.assertEqual(4, len(schema["anyOf"]))
        self.assertIn("CriterionAssessment", schema["$defs"])
        decision_action = action_schema_for(schema, "propose_decision")
        self.assertEqual(
            "AgentDecision",
            decision_action["properties"]["decision"]["title"],
        )

    def test_tool_schema_uses_only_allowlisted_tools(self) -> None:
        """Every allowed tool should receive one exact alternative."""

        schema = build_runtime_action_json_schema(ALLOWED_TOOLS)
        tool_names = {
            action_schema["properties"]["tool_name"]["const"]
            for action_schema in schema["anyOf"]
            if "tool_name" in action_schema["properties"]
        }
        self.assertEqual(set(ALLOWED_TOOLS), tool_names)

    def test_policy_tool_arguments_are_closed_and_required(self) -> None:
        """Policy retrieval must use its exact dispatcher contract."""

        schema = build_runtime_action_json_schema(ALLOWED_TOOLS)
        tool_action = tool_schema_for(schema, MEDICAL_POLICY_TOOL)
        arguments = tool_action["properties"]["tool_arguments"]

        self.assertIs(False, arguments["additionalProperties"])
        self.assertEqual(
            {"payer", "procedure_code", "service_date", "query"},
            set(arguments["required"]),
        )
        self.assertEqual(
            set(arguments["required"]), set(arguments["properties"])
        )

    def test_decision_schema_is_closed_and_fully_required(self) -> None:
        """Structured output must request the complete decision contract."""

        schema = build_runtime_action_json_schema(ALLOWED_TOOLS)
        decision_action = action_schema_for(schema, "propose_decision")
        decision = decision_action["properties"]["decision"]

        self.assertIs(False, decision["additionalProperties"])
        self.assertEqual(
            set(decision["properties"]), set(decision["required"])
        )

        criterion = schema["$defs"]["CriterionAssessment"]
        self.assertIs(False, criterion["additionalProperties"])
        self.assertEqual(
            set(criterion["properties"]), set(criterion["required"])
        )

    def test_schema_builders_return_independent_mappings(self) -> None:
        """A caller must not mutate future response configurations."""

        first_format = build_runtime_action_response_format(ALLOWED_TOOLS)
        first_format["json_schema"]["name"] = "changed"
        second_format = build_runtime_action_response_format(ALLOWED_TOOLS)

        self.assertEqual(
            "runtime_action", second_format["json_schema"]["name"]
        )
        self.assertIsNot(first_format, second_format)

    def test_rejects_unknown_or_duplicate_tool_names(self) -> None:
        """Unspecified tools cannot receive a provider schema."""

        invalid_allowlists = (
            ("unknown_tool",),
            (MEDICAL_POLICY_TOOL, MEDICAL_POLICY_TOOL),
        )
        for allowlist in invalid_allowlists:
            with self.subTest(allowlist=allowlist):
                with self.assertRaises(ValueError):
                    build_runtime_action_json_schema(allowlist)

    def test_action_payloads_remain_runtime_parser_compatible(self) -> None:
        """Provider-shaped actions must still cross the parser boundary."""

        payloads_and_types = (
            (
                {
                    "action": "call_tool",
                    "tool_name": MEDICAL_POLICY_TOOL,
                    "tool_arguments": {
                        "payer": "BCBS_AZ",
                        "procedure_code": "70553",
                        "service_date": "2026-06-02",
                        "query": "MRI medical necessity",
                    },
                },
                RuntimeActionType.CALL_TOOL,
            ),
            (
                {
                    "action": "propose_decision",
                    "decision": make_test_agent_decision_payload(),
                },
                RuntimeActionType.PROPOSE_DECISION,
            ),
            (
                {
                    "action": "request_manual_review",
                    "reason": "Required evidence is unavailable.",
                },
                RuntimeActionType.REQUEST_MANUAL_REVIEW,
            ),
        )

        for payload, expected_type in payloads_and_types:
            with self.subTest(action=payload["action"]):
                parsed = parse_model_action(json.dumps(payload))
                self.assertEqual(expected_type, parsed.action_type)

    def test_schema_is_deterministic_across_allowlist_order(self) -> None:
        """Equivalent allowlists should serialize identically."""

        first_schema = build_runtime_action_json_schema(ALLOWED_TOOLS)
        second_schema = build_runtime_action_json_schema(
            tuple(reversed(ALLOWED_TOOLS))
        )
        self.assertEqual(
            json.dumps(first_schema, sort_keys=True, separators=(",", ":")),
            json.dumps(second_schema, sort_keys=True, separators=(",", ":")),
        )


if __name__ == "__main__":
    unittest.main()
