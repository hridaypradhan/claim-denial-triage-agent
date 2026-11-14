"""Tests for the OpenAI-compatible Chat Completions transport."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any

from claim_agent.agent.hosted_model import (
    HostedModelTransportRequest,
)
from claim_agent.agent.openai_chat_transport import (
    OpenAIChatTransportSettings,
    OpenAICompatibleChatTransport,
    OpenAICompatibleProviderError,
)
from claim_agent.tools.dispatcher import (
    ENCOUNTER_NOTE_TOOL,
    MEDICAL_POLICY_TOOL,
)

REQUEST_ID = "run-openai-compatible:model:1"
MODEL_NAME = "mimo-v2-6-pro"
BASE_URL = "https://openai.rc.asu.edu/v1"


@dataclass
class FakeMessage:
    """Minimal assistant message returned by the fake SDK."""

    content: str | None


@dataclass
class FakeChoice:
    """Minimal completion choice returned by the fake SDK."""

    message: FakeMessage


@dataclass
class FakeUsage:
    """Minimal token usage returned by the fake SDK."""

    prompt_tokens: int
    completion_tokens: int


@dataclass
class FakeChatCompletion:
    """Minimal successful chat-completion response."""

    choices: list[FakeChoice]
    usage: FakeUsage | None = None
    id: str | None = None
    _request_id: str | None = None


class RecordingCompletions:
    """Record SDK arguments and return a configured result."""

    def __init__(
        self,
        response: FakeChatCompletion | None = None,
        error: Exception | None = None,
    ) -> None:
        """Store deterministic fake behavior."""

        self.response = response
        self.error = error
        self.received_arguments: list[dict[str, Any]] = []

    def create(self, **arguments: Any) -> FakeChatCompletion:
        """Record one request before returning or raising."""

        self.received_arguments.append(arguments)

        if self.error is not None:
            raise self.error

        if self.response is None:
            raise AssertionError("The fake client requires a response")

        return self.response


class FakeChat:
    """Expose the fake completions resource."""

    def __init__(self, completions: RecordingCompletions) -> None:
        """Store the completions resource."""

        self.completions = completions


class FakeOpenAIClient:
    """Expose the SDK shape used by the transport."""

    def __init__(self, completions: RecordingCompletions) -> None:
        """Create the fake chat namespace."""

        self.chat = FakeChat(completions)


def make_settings(
    *,
    response_format_mode: str = "json_schema",
) -> OpenAIChatTransportSettings:
    """Return validated non-secret transport settings."""

    return OpenAIChatTransportSettings(
        base_url=BASE_URL,
        max_completion_tokens=4096,
        reasoning_effort="low",
        temperature=0.0,
        response_format_mode=response_format_mode,
    )


def make_transport_request(
    *,
    allowed_tool_names: tuple[str, ...] = (
        ENCOUNTER_NOTE_TOOL,
        MEDICAL_POLICY_TOOL,
    ),
    request_id: str = REQUEST_ID,
) -> HostedModelTransportRequest:
    """Return one provider-neutral hosted request."""

    return HostedModelTransportRequest(
        request_id=request_id,
        model_name=MODEL_NAME,
        system_instructions=(
            "Use only retrieved evidence and return one JSON action."
        ),
        task_context=("Review the synthetic medical-necessity denial."),
        allowed_tool_names=allowed_tool_names,
        timeout_seconds=30.0,
    )


class OpenAICompatibleChatTransportTests(unittest.TestCase):
    """Verify request translation, metadata, and safe failures."""

    def test_successful_request_is_translated(self) -> None:
        """The transport should preserve runtime and provider settings."""

        completions = RecordingCompletions(
            response=FakeChatCompletion(
                choices=[
                    FakeChoice(
                        message=FakeMessage(
                            content=(
                                '{"action":"request_manual_review",'
                                '"reason":"Evidence is unavailable."}'
                            )
                        )
                    )
                ],
                usage=FakeUsage(
                    prompt_tokens=125,
                    completion_tokens=19,
                ),
                id="chat-completion-123",
                _request_id="provider-request-123",
            )
        )
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(),
        )

        result = transport.generate(make_transport_request())

        self.assertEqual(1, len(completions.received_arguments))
        arguments = completions.received_arguments[0]

        self.assertEqual(MODEL_NAME, arguments["model"])
        self.assertEqual(4096, arguments["max_completion_tokens"])
        self.assertEqual("low", arguments["reasoning_effort"])
        self.assertEqual(0.0, arguments["temperature"])
        self.assertEqual(30.0, arguments["timeout"])
        self.assertNotIn("tools", arguments)

        self.assertEqual(
            {
                "X-Client-Request-Id": REQUEST_ID,
                "x-obleth-boons": "off",
            },
            arguments["extra_headers"],
        )
        self.assertEqual(
            "system",
            arguments["messages"][0]["role"],
        )
        self.assertEqual(
            "Use only retrieved evidence and return one JSON action.",
            arguments["messages"][0]["content"],
        )
        self.assertEqual(
            "Review the synthetic medical-necessity denial.",
            arguments["messages"][1]["content"],
        )

        self.assertEqual(
            (
                '{"action":"request_manual_review",'
                '"reason":"Evidence is unavailable."}'
            ),
            result.response_text,
        )
        self.assertEqual(
            "provider-request-123",
            result.provider_request_id,
        )
        self.assertEqual(125, result.input_token_count)
        self.assertEqual(19, result.output_token_count)

    def test_response_format_is_built_from_request_allowlist(
        self,
    ) -> None:
        """Each request should receive only its authorized tool schema."""

        completions = RecordingCompletions(
            response=FakeChatCompletion(
                choices=[
                    FakeChoice(
                        message=FakeMessage(
                            content=(
                                '{"action":"request_manual_review",'
                                '"reason":"Evidence is unavailable."}'
                            )
                        )
                    )
                ]
            )
        )
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(),
        )

        transport.generate(
            make_transport_request(allowed_tool_names=(MEDICAL_POLICY_TOOL,))
        )

        response_format = completions.received_arguments[0]["response_format"]
        schema = response_format["json_schema"]["schema"]
        tool_names = {
            action_schema["properties"]["tool_name"]["const"]
            for action_schema in schema["anyOf"]
            if "tool_name" in action_schema["properties"]
        }

        self.assertEqual(
            {MEDICAL_POLICY_TOOL},
            tool_names,
        )
        self.assertIs(
            True,
            response_format["json_schema"]["strict"],
        )

    def test_json_object_mode_omits_strict_schema(
        self,
    ) -> None:
        """A model without Schema support should receive JSON mode."""

        completions = RecordingCompletions(
            response=FakeChatCompletion(
                choices=[
                    FakeChoice(
                        message=FakeMessage(
                            content=(
                                '{"action":"request_manual_review",'
                                '"reason":"Evidence is unavailable."}'
                            )
                        )
                    )
                ]
            )
        )
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(
                response_format_mode="json_object"
            ),
        )

        transport.generate(make_transport_request())

        self.assertEqual(
            {
                "type": "json_object",
            },
            completions.received_arguments[0][
                "response_format"
            ],
        )

    def test_empty_allowlist_exposes_no_tool_action(self) -> None:
        """A tool-less request should allow only decision or review."""

        completions = RecordingCompletions(
            response=FakeChatCompletion(
                choices=[
                    FakeChoice(
                        message=FakeMessage(
                            content=(
                                '{"action":"request_manual_review",'
                                '"reason":"No tool is authorized."}'
                            )
                        )
                    )
                ]
            )
        )
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(),
        )

        transport.generate(
            make_transport_request(allowed_tool_names=())
        )

        schema = completions.received_arguments[0][
            "response_format"
        ]["json_schema"]["schema"]
        tool_actions = [
            action_schema
            for action_schema in schema["anyOf"]
            if "tool_name" in action_schema["properties"]
        ]

        self.assertEqual([], tool_actions)
        self.assertEqual(2, len(schema["anyOf"]))

    def test_sequential_requests_do_not_reuse_tool_schema(
        self,
    ) -> None:
        """A later route must not inherit an earlier route's tools."""

        completions = RecordingCompletions(
            response=FakeChatCompletion(
                choices=[
                    FakeChoice(
                        message=FakeMessage(
                            content=(
                                '{"action":"request_manual_review",'
                                '"reason":"Evidence is unavailable."}'
                            )
                        )
                    )
                ]
            )
        )
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(),
        )

        transport.generate(
            make_transport_request(
                allowed_tool_names=(MEDICAL_POLICY_TOOL,),
                request_id="route-one:model:1",
            )
        )
        transport.generate(
            make_transport_request(
                allowed_tool_names=(ENCOUNTER_NOTE_TOOL,),
                request_id="route-two:model:1",
            )
        )

        observed_tool_sets = []
        for arguments in completions.received_arguments:
            schema = arguments["response_format"][
                "json_schema"
            ]["schema"]
            observed_tool_sets.append(
                {
                    action_schema["properties"][
                        "tool_name"
                    ]["const"]
                    for action_schema in schema["anyOf"]
                    if "tool_name"
                    in action_schema["properties"]
                }
            )

        self.assertEqual(
            [
                {MEDICAL_POLICY_TOOL},
                {ENCOUNTER_NOTE_TOOL},
            ],
            observed_tool_sets,
        )

    def test_unknown_tool_fails_before_provider_call(
        self,
    ) -> None:
        """A tool without a schema must not reach the provider."""

        completions = RecordingCompletions(
            response=FakeChatCompletion(
                choices=[
                    FakeChoice(
                        message=FakeMessage(
                            content=(
                                '{"action":"request_manual_review",'
                                '"reason":"Evidence is unavailable."}'
                            )
                        )
                    )
                ]
            )
        )
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(),
        )

        with self.assertRaisesRegex(
            ValueError,
            "unknown_tool",
        ):
            transport.generate(
                make_transport_request(
                    allowed_tool_names=("unknown_tool",)
                )
            )

        self.assertEqual(
            [],
            completions.received_arguments,
        )

    def test_missing_usage_remains_unknown(self) -> None:
        """Unreported token counts must not be replaced with zero."""

        completions = RecordingCompletions(
            response=FakeChatCompletion(
                choices=[
                    FakeChoice(
                        message=FakeMessage(
                            content=(
                                '{"action":"request_manual_review",'
                                '"reason":"Usage unavailable."}'
                            )
                        )
                    )
                ],
                usage=None,
                id="chat-completion-without-usage",
            )
        )
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
            client=FakeOpenAIClient(completions),
            settings=make_settings(),
        )

        with self.assertRaises(TimeoutError):
            transport.generate(make_transport_request())

    def test_provider_failure_is_sanitized(self) -> None:
        """Provider exception text must not cross the boundary."""

        completions = RecordingCompletions(
            error=RuntimeError("Authorization: Bearer secret-value")
        )
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(),
        )

        with self.assertRaisesRegex(
            OpenAICompatibleProviderError,
            "RuntimeError",
        ) as raised_context:
            transport.generate(make_transport_request())

        self.assertNotIn(
            "secret-value",
            str(raised_context.exception),
        )

    def test_empty_provider_response_is_rejected(self) -> None:
        """An empty response must not enter the action parser."""

        completions = RecordingCompletions(
            response=FakeChatCompletion(
                choices=[FakeChoice(message=FakeMessage(content=None))]
            )
        )
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(),
        )

        with self.assertRaisesRegex(
            OpenAICompatibleProviderError,
            "assistant text",
        ):
            transport.generate(make_transport_request())

    def test_invalid_response_format_mode_is_rejected(
        self,
    ) -> None:
        """Unknown provider output modes must fail at setup."""

        with self.assertRaisesRegex(
            ValueError,
            "response_format_mode",
        ):
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
                with self.assertRaisesRegex(
                    ValueError,
                    settings["message"],
                ):
                    OpenAIChatTransportSettings(
                        base_url=settings["base_url"],
                        max_completion_tokens=(settings["max_completion_tokens"]),
                        reasoning_effort=settings["reasoning_effort"],
                        temperature=settings["temperature"],
                    )


if __name__ == "__main__":
    unittest.main()
