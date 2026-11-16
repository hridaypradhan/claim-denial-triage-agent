"""Tests for deterministic grounded-drafting prompt construction."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from claim_agent.agent.drafting.prompt import (
    DRAFTING_PROMPT_VERSION,
    DRAFTING_SYSTEM_INSTRUCTIONS,
    build_drafting_request,
)
from claim_agent.contracts import DraftingInput


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "contracts"
    / "drafting"
    / "valid_draft_input.json"
)


def load_drafting_input() -> DraftingInput:
    """Load the shared authorized drafting fixture."""

    return DraftingInput.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))


def extract_context_payload(task_context: str) -> dict[str, object]:
    """Extract the canonical JSON enclosed by drafting boundaries."""

    prefix = "BEGIN DRAFTING INPUT\n"
    suffix = "\nEND DRAFTING INPUT"
    if not task_context.startswith(prefix) or not task_context.endswith(suffix):
        raise AssertionError("Drafting context boundaries are missing")
    return json.loads(task_context[len(prefix) : -len(suffix)])


class DraftingPromptTests(unittest.TestCase):
    """Verify frozen inputs and conservative drafting instructions."""

    def test_prompt_version_is_explicit(self) -> None:
        self.assertEqual("drafting-v1", DRAFTING_PROMPT_VERSION)

    def test_instructions_preserve_grounding_and_human_review(self) -> None:
        required_phrases = (
            "no tool access",
            "current run",
            "untrusted data",
            "Do not invent",
            "exact normalized quote",
            "PENDING_REVIEW",
            "JSON only",
        )
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, DRAFTING_SYSTEM_INSTRUCTIONS)

    def test_request_contains_only_the_authorized_drafting_input(self) -> None:
        drafting_input = load_drafting_input()
        request = build_drafting_request(
            request_id="draft-request-1",
            drafting_input=drafting_input,
        )
        payload = extract_context_payload(request.task_context)

        self.assertEqual(
            {
                "bundle_fingerprint",
                "contract_version",
                "evidence_bundle",
                "validation_result",
                "verifier_result",
            },
            set(payload),
        )
        self.assertEqual(
            drafting_input.bundle_fingerprint,
            payload["bundle_fingerprint"],
        )
        self.assertEqual(
            drafting_input.run_id,
            payload["evidence_bundle"]["run_id"],
        )

    def test_context_serialization_is_deterministic(self) -> None:
        drafting_input = load_drafting_input()
        first = build_drafting_request(
            request_id="draft-request-1",
            drafting_input=drafting_input,
        )
        second = build_drafting_request(
            request_id="draft-request-1",
            drafting_input=drafting_input,
        )
        self.assertEqual(first.task_context, second.task_context)


if __name__ == "__main__":
    unittest.main()
