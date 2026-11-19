"""Tests for the provider-independent drafting boundary."""

from __future__ import annotations

import unittest

from claim_agent.agent.drafting.interface import DraftingRequest, DraftingResponse


class DraftingInterfaceTests(unittest.TestCase):
    """Reject malformed requests and responses before orchestration."""

    def test_request_requires_nonblank_identity_and_context(self) -> None:
        for field_name, values in (
            ("request_id", (" ", "instructions", "context")),
            ("system_instructions", ("request-1", " ", "context")),
            ("task_context", ("request-1", "instructions", " ")),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    DraftingRequest(*values)

    def test_response_rejects_blank_output_and_negative_tokens(self) -> None:
        with self.assertRaisesRegex(ValueError, "response_text"):
            DraftingResponse(request_id="request-1", response_text=" ")

        with self.assertRaisesRegex(ValueError, "token counts"):
            DraftingResponse(
                request_id="request-1",
                response_text="{}",
                output_token_count=-1,
            )


if __name__ == "__main__":
    unittest.main()
