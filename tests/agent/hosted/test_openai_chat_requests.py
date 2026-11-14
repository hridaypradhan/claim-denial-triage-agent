"""Request-building tests for the OpenAI-compatible chat transport."""

from __future__ import annotations

import unittest

from claim_agent.agent.hosted.openai_chat import OpenAICompatibleChatTransport
from claim_agent.tools.dispatcher import ENCOUNTER_NOTE_TOOL, MEDICAL_POLICY_TOOL
from tests.agent.hosted.openai_chat_test_support import (
    BASE_URL,
    MODEL_NAME,
    REQUEST_ID,
    FakeChatCompletion,
    FakeChoice,
    FakeMessage,
    FakeOpenAIClient,
    FakeUsage,
    RecordingCompletions,
    make_settings,
    make_transport_request,
    manual_review_response,
)


class OpenAIChatRequestTests(unittest.TestCase):
    """Verify request translation and route-specific schemas."""

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
                usage=FakeUsage(prompt_tokens=125, completion_tokens=19),
                id="chat-completion-123",
                _request_id="provider-request-123",
            )
        )
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(),
        )

        result = transport.generate(make_transport_request())
        arguments = completions.received_arguments[0]

        self.assertEqual(1, len(completions.received_arguments))
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
        self.assertEqual("system", arguments["messages"][0]["role"])
        self.assertEqual(
            "Use only retrieved evidence and return one JSON action.",
            arguments["messages"][0]["content"],
        )
        self.assertEqual(
            "Review the synthetic medical-necessity denial.",
            arguments["messages"][1]["content"],
        )
        self.assertEqual("provider-request-123", result.provider_request_id)
        self.assertEqual(125, result.input_token_count)
        self.assertEqual(19, result.output_token_count)

    def test_response_format_is_built_from_request_allowlist(self) -> None:
        """Each request should receive only its authorized tool schema."""

        completions = RecordingCompletions(response=manual_review_response())
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
        self.assertEqual({MEDICAL_POLICY_TOOL}, tool_names)
        self.assertIs(True, response_format["json_schema"]["strict"])

    def test_json_object_mode_omits_strict_schema(self) -> None:
        """A model without Schema support should receive JSON mode."""

        completions = RecordingCompletions(response=manual_review_response())
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(response_format_mode="json_object"),
        )
        transport.generate(make_transport_request())
        self.assertEqual(
            {"type": "json_object"},
            completions.received_arguments[0]["response_format"],
        )

    def test_empty_allowlist_exposes_no_tool_action(self) -> None:
        """A tool-less request should allow only decision or review."""

        completions = RecordingCompletions(response=manual_review_response())
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(),
        )
        transport.generate(make_transport_request(allowed_tool_names=()))

        schema = completions.received_arguments[0]["response_format"][
            "json_schema"
        ]["schema"]
        tool_actions = [
            action_schema
            for action_schema in schema["anyOf"]
            if "tool_name" in action_schema["properties"]
        ]
        self.assertEqual([], tool_actions)
        self.assertEqual(2, len(schema["anyOf"]))

    def test_sequential_requests_do_not_reuse_tool_schema(self) -> None:
        """A later route must not inherit an earlier route's tools."""

        completions = RecordingCompletions(response=manual_review_response())
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
            schema = arguments["response_format"]["json_schema"]["schema"]
            observed_tool_sets.append(
                {
                    action_schema["properties"]["tool_name"]["const"]
                    for action_schema in schema["anyOf"]
                    if "tool_name" in action_schema["properties"]
                }
            )
        self.assertEqual(
            [{MEDICAL_POLICY_TOOL}, {ENCOUNTER_NOTE_TOOL}],
            observed_tool_sets,
        )

    def test_unknown_tool_fails_before_provider_call(self) -> None:
        """A tool without a schema must not reach the provider."""

        completions = RecordingCompletions(response=manual_review_response())
        transport = OpenAICompatibleChatTransport(
            client=FakeOpenAIClient(completions),
            settings=make_settings(),
        )
        with self.assertRaisesRegex(ValueError, "unknown_tool"):
            transport.generate(
                make_transport_request(allowed_tool_names=("unknown_tool",))
            )
        self.assertEqual([], completions.received_arguments)


if __name__ == "__main__":
    unittest.main()
