"""Tests for deterministic, same-run verifier prompt construction."""

from __future__ import annotations

import json
import unittest

from claim_agent.agent.verification.prompt import (
    VERIFIER_PROMPT_VERSION,
    VERIFIER_SYSTEM_INSTRUCTIONS,
    build_verifier_request,
)
from tests.agent.verification.support import (
    REQUEST_ID,
    load_verifier_input,
)


def extract_context_payload(task_context: str) -> dict[str, object]:
    """Extract the canonical JSON enclosed by verifier boundaries."""

    prefix = "BEGIN VERIFIER INPUT\n"
    suffix = "\nEND VERIFIER INPUT"

    if not task_context.startswith(prefix):
        raise AssertionError("Verifier context is missing its opening boundary")

    if not task_context.endswith(suffix):
        raise AssertionError("Verifier context is missing its closing boundary")

    serialized_payload = task_context[len(prefix) : -len(suffix)]
    return json.loads(serialized_payload)


class VerifierPromptTests(unittest.TestCase):
    """Verify bounded instructions and same-run input serialization."""

    def test_prompt_version_is_explicit(self) -> None:
        """Verifier prompt changes should be traceable."""

        self.assertEqual("verifier-v1", VERIFIER_PROMPT_VERSION)

    def test_system_instructions_forbid_tools_and_external_evidence(
        self,
    ) -> None:
        """The verifier must remain tool-less and same-run bounded."""

        required_phrases = (
            "no tool access",
            "current run",
            "evidence bundle",
            "untrusted data",
            "Do not invent",
            "AGREE",
            "DISAGREE",
            "UNCERTAIN",
            "JSON only",
        )

        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(
                    phrase,
                    VERIFIER_SYSTEM_INSTRUCTIONS,
                )

    def test_request_contains_only_frozen_verifier_input(self) -> None:
        """The context should contain the decision and its exact bundle."""

        verifier_input = load_verifier_input()

        request = build_verifier_request(
            request_id=REQUEST_ID,
            verifier_input=verifier_input,
        )
        payload = extract_context_payload(request.task_context)

        self.assertEqual(REQUEST_ID, request.request_id)
        self.assertEqual(
            VERIFIER_SYSTEM_INSTRUCTIONS,
            request.system_instructions,
        )
        self.assertEqual(
            {
                "authorized_tools",
                "bundle_fingerprint",
                "contract_version",
                "evidence_bundle",
                "proposed_decision",
            },
            set(payload),
        )
        self.assertEqual([], payload["authorized_tools"])
        self.assertEqual(
            verifier_input.bundle_fingerprint,
            payload["bundle_fingerprint"],
        )
        self.assertEqual(
            verifier_input.run_id,
            payload["evidence_bundle"]["run_id"],
        )
        self.assertEqual(
            verifier_input.knowledge_snapshot_id,
            payload["evidence_bundle"]["knowledge_snapshot_id"],
        )
        self.assertEqual(
            verifier_input.proposed_decision.decision_id,
            payload["proposed_decision"]["decision_id"],
        )

    def test_context_serialization_is_deterministic(self) -> None:
        """Equivalent inputs should produce byte-identical task context."""

        verifier_input = load_verifier_input()

        first_request = build_verifier_request(
            request_id=REQUEST_ID,
            verifier_input=verifier_input,
        )
        second_request = build_verifier_request(
            request_id=REQUEST_ID,
            verifier_input=verifier_input,
        )

        self.assertEqual(
            first_request.task_context,
            second_request.task_context,
        )

    def test_request_id_validation_remains_at_interface_boundary(
        self,
    ) -> None:
        """Blank request identities should fail before a provider call."""

        with self.assertRaisesRegex(ValueError, "request_id"):
            build_verifier_request(
                request_id="   ",
                verifier_input=load_verifier_input(),
            )


if __name__ == "__main__":
    unittest.main()
