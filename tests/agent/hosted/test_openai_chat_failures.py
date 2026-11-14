"""Failure and validation tests for the hosted chat transport."""

from __future__ import annotations

import unittest

from claim_agent.agent.hosted.openai_chat import (
    OpenAIChatTransportSettings,
    OpenAICompatibleChatTransport,
    OpenAICompatibleProviderError,
)
from tests.agent.hosted.openai_chat_test_support import (
    BASE_URL,
    FakeChatCompletion,
    FakeChoice,
    FakeMessage,
    FakeOpenAIClient,
    RecordingCompletions,
    make_settings,
    make_transport_request,
    manual_review_response,
)


class OpenAIChatFailureTests(unittest.TestCase):
    """Verify response metadata, safe failures, and configuration."""

    def test_missing_usage_remains_unknown(self) -> None:
        """Unreported token counts must not be replaced with zero."""

        response = manual_review_response("Usage unavailable.")
        response.id = "chat-completion-without-usage"
        completions = RecordingCompletions(response=response)
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(),
        )

        result = transport.generate(make_transport_request())
        self.assertIsNone(result.input_token_count)
        self.assertIsNone(result.output_token_count)
        self.assertEqual(
            "chat-completion-without-usage",
            result.provider_request_id,
        )

    def test_builtin_timeout_is_preserved(self) -> None:
        """A timeout must remain recognizable to the hosted client."""

        completions = RecordingCompletions(
            error=TimeoutError("Synthetic provider timeout")
        )
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions), settings=make_settings()
        )
        with self.assertRaises(TimeoutError):
            transport.generate(make_transport_request())

    def test_provider_failure_is_sanitized(self) -> None:
        """Provider exception text must not cross the boundary."""

        completions = RecordingCompletions(
            error=RuntimeError("Authorization: Bearer secret-value")
        )
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions), settings=make_settings()
        )
        with self.assertRaisesRegex(
            OpenAICompatibleProviderError, "RuntimeError"
        ) as raised_context:
            transport.generate(make_transport_request())
        self.assertNotIn("secret-value", str(raised_context.exception))

    def test_empty_provider_response_is_rejected(self) -> None:
        """An empty response must not enter the action parser."""

        completions = RecordingCompletions(
            response=FakeChatCompletion(
                choices=[FakeChoice(message=FakeMessage(content=None))]
            )
        )
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions), settings=make_settings()
        )
        with self.assertRaisesRegex(
            OpenAICompatibleProviderError, "assistant text"
        ):
            transport.generate(make_transport_request())

    def test_invalid_response_format_mode_is_rejected(self) -> None:
        """Unknown provider output modes must fail at setup."""

        with self.assertRaisesRegex(ValueError, "response_format_mode"):
            OpenAIChatTransportSettings(
                base_url=BASE_URL,
                max_completion_tokens=4096,
                reasoning_effort="low",
                temperature=0.0,
                response_format_mode="automatic",
            )

    def test_invalid_settings_are_rejected(self) -> None:
        """Invalid bounded-request settings should fail immediately."""

        invalid_settings = (
            {
                "base_url": " ",
                "max_completion_tokens": 4096,
                "reasoning_effort": "low",
                "temperature": 0.0,
                "message": "base_url",
            },
            {
                "base_url": BASE_URL,
                "max_completion_tokens": 0,
                "reasoning_effort": "low",
                "temperature": 0.0,
                "message": "max_completion_tokens",
            },
            {
                "base_url": BASE_URL,
                "max_completion_tokens": 4096,
                "reasoning_effort": "extreme",
                "temperature": 0.0,
                "message": "reasoning_effort",
            },
            {
                "base_url": BASE_URL,
                "max_completion_tokens": 4096,
                "reasoning_effort": "low",
                "temperature": -0.1,
                "message": "temperature",
            },
        )
        for settings in invalid_settings:
            with self.subTest(settings=settings):
                with self.assertRaisesRegex(ValueError, settings["message"]):
                    OpenAIChatTransportSettings(
                        base_url=settings["base_url"],
                        max_completion_tokens=settings[
                            "max_completion_tokens"
                        ],
                        reasoning_effort=settings["reasoning_effort"],
                        temperature=settings["temperature"],
                    )


if __name__ == "__main__":
    unittest.main()
