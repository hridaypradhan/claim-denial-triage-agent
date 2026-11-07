"""Tests for safe default-deny trace payload redaction."""

from __future__ import annotations

import hashlib
import json
import unittest

from claim_agent.agent.trace_redaction import (
    REDACTED_VALUE,
    calculate_redacted_value_sha256,
    redact_trace_payload,
)


class TraceRedactionTests(unittest.TestCase):
    """Verify that traces retain codes while excluding unrestricted text."""

    def test_allowed_identifiers_and_codes_are_preserved(self) -> None:
        """Evaluation identifiers and controlled outcomes must remain usable."""

        payload = {
            "claim_id": "CLM-2026-005097",
            "service_line_id": 1,
            "outcome": "ACCEPTED",
            "passage_ids": ["POL-1@v1#P1"],
        }

        self.assertEqual(redact_trace_payload(payload), payload)

    def test_free_text_is_replaced_and_hashed(self) -> None:
        """A rationale should be comparable without exposing its contents."""

        payload = {"rationale": "Synthetic unrestricted explanation."}

        redacted = redact_trace_payload(payload)

        self.assertEqual(redacted["rationale"], REDACTED_VALUE)
        self.assertEqual(
            redacted["rationale_sha256"],
            hashlib.sha256(payload["rationale"].encode("utf-8")).hexdigest(),
        )

    def test_free_text_lists_preserve_length_and_receive_one_hash(self) -> None:
        """Action counts remain visible while their wording stays private."""

        actions = [
            "Prepare the appeal.",
            "Request human review.",
        ]

        redacted = redact_trace_payload({"actions": actions})

        self.assertEqual(
            redacted["actions"],
            [REDACTED_VALUE, REDACTED_VALUE],
        )
        self.assertEqual(
            redacted["actions_sha256"],
            calculate_redacted_value_sha256(actions),
        )

    def test_nested_payload_is_filtered_recursively(self) -> None:
        """Sensitive fields must not escape through an approved container."""

        payload = {
            "decision": {
                "decision_id": "decision-1",
                "rationale": "Synthetic rationale.",
                "api_key": "secret-value",
            }
        }

        redacted = redact_trace_payload(payload)

        self.assertEqual(
            redacted["decision"]["decision_id"],
            "decision-1",
        )
        self.assertEqual(
            redacted["decision"]["rationale"],
            REDACTED_VALUE,
        )
        self.assertEqual(
            redacted["decision"]["api_key"],
            REDACTED_VALUE,
        )
        self.assertNotIn(
            "secret-value",
            json.dumps(redacted),
        )

    def test_unknown_fields_are_default_denied(self) -> None:
        """A newly introduced field must be reviewed before retaining values."""

        redacted = redact_trace_payload({"unreviewed_provider_field": "provider data"})

        self.assertEqual(
            redacted["unreviewed_provider_field"],
            REDACTED_VALUE,
        )

    def test_structure_keys_do_not_authorize_scalar_content(self) -> None:
        """Malformed scalar structures must remain default denied."""

        payloads = (
            {"decision": "Patient Jane Doe DOB 1970-01-01 needs MRI"},
            {"tool_calls": ["call retrieve for Jane Doe"]},
            {"criterion_assessments": ["free text note"]},
        )

        for payload in payloads:
            with self.subTest(payload=payload):
                redacted = redact_trace_payload(payload)
                self.assertNotIn("Jane Doe", json.dumps(redacted))
                self.assertIn(REDACTED_VALUE, json.dumps(redacted))

    def test_tool_call_counter_accepts_only_integer_scalar(self) -> None:
        """The overloaded tool_calls key must not expose non-counter values."""

        self.assertEqual(
            2,
            redact_trace_payload({"tool_calls": 2})["tool_calls"],
        )

        for unsafe_value in ("Jane Doe", True):
            with self.subTest(unsafe_value=unsafe_value):
                self.assertEqual(
                    REDACTED_VALUE,
                    redact_trace_payload(
                        {"tool_calls": unsafe_value}
                    )["tool_calls"],
                )

    def test_only_valid_sha256_values_are_preserved(self) -> None:
        """A hash-named field must contain an actual lowercase SHA-256."""

        valid_hash = "a" * 64

        self.assertEqual(
            valid_hash,
            redact_trace_payload({"note_sha256": valid_hash})["note_sha256"],
        )
        self.assertEqual(
            REDACTED_VALUE,
            redact_trace_payload(
                {"note_sha256": "Jane Doe, member ID 12345"}
            )["note_sha256"],
        )
