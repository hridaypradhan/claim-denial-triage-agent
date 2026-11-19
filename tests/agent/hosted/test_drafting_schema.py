"""Tests for the hosted grounded-draft response schema."""

from __future__ import annotations

import unittest

from claim_agent.agent.hosted.drafting_schema import (
    build_appeal_draft_json_schema,
    build_appeal_draft_response_format,
)


class AppealDraftSchemaTests(unittest.TestCase):
    """Verify that hosted drafting uses the frozen #51 contract."""

    def test_builds_named_strict_response_format(self) -> None:
        response_format = build_appeal_draft_response_format()

        self.assertEqual("json_schema", response_format["type"])
        self.assertEqual(
            "appeal_draft",
            response_format["json_schema"]["name"],
        )
        self.assertIs(True, response_format["json_schema"]["strict"])

    def test_schema_uses_complete_appeal_draft_contract(self) -> None:
        schema = build_appeal_draft_json_schema()

        self.assertEqual("AppealDraft", schema["title"])
        self.assertIs(False, schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), set(schema["required"]))
        self.assertEqual(
            {
                "contract_version",
                "draft_id",
                "decision_id",
                "claim_id",
                "service_line_id",
                "disposition",
                "approval_state",
                "subject",
                "body",
                "grounded_claims",
                "citation_ids",
            },
            set(schema["properties"]),
        )

    def test_nested_grounded_claim_objects_are_closed(self) -> None:
        schema = build_appeal_draft_json_schema()
        grounded_claim_ref = schema["properties"]["grounded_claims"]["items"]["$ref"]
        definition_name = grounded_claim_ref.rsplit("/", 1)[-1]
        grounded_claim_schema = schema["$defs"][definition_name]

        self.assertIs(False, grounded_claim_schema["additionalProperties"])
        self.assertEqual(
            set(grounded_claim_schema["properties"]),
            set(grounded_claim_schema["required"]),
        )


if __name__ == "__main__":
    unittest.main()
