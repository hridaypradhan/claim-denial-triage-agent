"""Tests for the hosted tool-less verifier response schema."""

from __future__ import annotations

import unittest

from claim_agent.agent.hosted.verifier_schema import (
    build_verifier_report_json_schema,
    build_verifier_report_response_format,
)


class VerifierReportSchemaTests(unittest.TestCase):
    """Verify that hosted verification uses the frozen #50 contract."""

    def test_builds_strict_verifier_response_format(self) -> None:
        """The provider should receive one named strict JSON schema."""

        response_format = build_verifier_report_response_format()

        self.assertEqual("json_schema", response_format["type"])
        self.assertEqual(
            "verifier_report",
            response_format["json_schema"]["name"],
        )
        self.assertIs(True, response_format["json_schema"]["strict"])

    def test_schema_uses_complete_verifier_report_contract(self) -> None:
        """Every VerifierReport field should be required and closed."""

        schema = build_verifier_report_json_schema()

        self.assertEqual("VerifierReport", schema["title"])
        self.assertIs(False, schema["additionalProperties"])
        self.assertEqual(
            set(schema["properties"]),
            set(schema["required"]),
        )

        expected_fields = {
            "contract_version",
            "decision_id",
            "claim_id",
            "service_line_id",
            "outcome",
            "rationale",
            "supported_criterion_ids",
            "disputed_criterion_ids",
            "unresolved_questions",
            "citation_ids",
        }
        self.assertEqual(expected_fields, set(schema["properties"]))

    def test_schema_exposes_no_action_or_tool_fields(self) -> None:
        """A verifier response must not request actions or tools."""

        schema = build_verifier_report_json_schema()
        properties = schema["properties"]

        prohibited_fields = {
            "action",
            "tool_name",
            "tool_arguments",
            "authorized_tools",
        }

        self.assertTrue(prohibited_fields.isdisjoint(properties))

    def test_schema_builders_return_independent_mappings(self) -> None:
        """Mutating one response format must not affect later calls."""

        first_format = build_verifier_report_response_format()
        first_format["json_schema"]["name"] = "changed"

        second_format = build_verifier_report_response_format()

        self.assertEqual(
            "verifier_report",
            second_format["json_schema"]["name"],
        )
        self.assertIsNot(first_format, second_format)


if __name__ == "__main__":
    unittest.main()