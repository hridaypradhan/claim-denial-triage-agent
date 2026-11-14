"""Tests for the frozen bounded-planning prompt."""

from __future__ import annotations

import re
import unittest

from claim_agent.agent.planning_prompt import (
    PLANNING_PROMPT_VERSION,
    PLANNING_SYSTEM_INSTRUCTIONS,
)


class PlanningPromptTests(unittest.TestCase):
    """Verify versioning and safety-critical planning instructions."""

    def setUp(self) -> None:
        """Normalize display wrapping for prose-level assertions."""

        self.normalized_prompt = " ".join(
            PLANNING_SYSTEM_INSTRUCTIONS.split()
        )

    def test_prompt_version_is_explicit(self) -> None:
        """Prompt changes must use a reviewable version identifier."""

        self.assertRegex(
            PLANNING_PROMPT_VERSION,
            re.compile(r"^planning-v[1-9][0-9]*$"),
        )

    def test_prompt_has_no_outer_whitespace(self) -> None:
        """Rendered prompt bytes should remain deterministic."""

        self.assertEqual(
            PLANNING_SYSTEM_INSTRUCTIONS,
            PLANNING_SYSTEM_INSTRUCTIONS.strip(),
        )

    def test_prompt_names_every_permitted_action(self) -> None:
        """The model should see the complete bounded action set."""

        permitted_actions = (
            "call_tool",
            "propose_decision",
            "request_manual_review",
        )

        for action_name in permitted_actions:
            with self.subTest(action_name=action_name):
                self.assertIn(
                    action_name,
                    PLANNING_SYSTEM_INSTRUCTIONS,
                )

    def test_prompt_preserves_evidence_boundaries(self) -> None:
        """The model must use only current-run evidence and citations."""

        required_phrases = (
            "current run",
            "untrusted data",
            "active claim",
            "Do not invent",
            "same-run tool observations",
            "missing, unavailable, conflicting",
        )

        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(
                    phrase,
                    self.normalized_prompt,
                )

    def test_prompt_preserves_human_review_boundary(self) -> None:
        """The model cannot approve or externally execute an outcome."""

        required_phrases = (
            "Never submit a claim or appeal",
            "pending deterministic validation",
            "human review",
            "Return JSON only",
        )

        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(
                    phrase,
                    self.normalized_prompt,
                )

    def test_prompt_is_provider_and_model_neutral(self) -> None:
        """The runtime prompt should not depend on one hosted service."""

        forbidden_names = (
            "Voyager",
            "OpenAI",
            "mimo-v2-6-pro",
            "glm-5-3",
            "deepseek-v4-1-flash",
        )

        for forbidden_name in forbidden_names:
            with self.subTest(forbidden_name=forbidden_name):
                self.assertNotIn(
                    forbidden_name,
                    PLANNING_SYSTEM_INSTRUCTIONS,
                )


if __name__ == "__main__":
    unittest.main()
