"""Shared fake SDK objects for hosted Chat Completions tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claim_agent.agent.hosted.client import HostedModelTransportRequest
from claim_agent.agent.hosted.openai_chat import OpenAIChatTransportSettings
from claim_agent.tools.dispatcher import ENCOUNTER_NOTE_TOOL, MEDICAL_POLICY_TOOL


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
        task_context="Review the synthetic medical-necessity denial.",
        allowed_tool_names=allowed_tool_names,
        timeout_seconds=30.0,
    )


def manual_review_response(
    reason: str = "Evidence is unavailable.",
) -> FakeChatCompletion:
    """Return one successful, structured manual-review response."""

    return FakeChatCompletion(
        choices=[
            FakeChoice(
                message=FakeMessage(
                    content=(
                        '{"action":"request_manual_review",'
                        f'"reason":"{reason}"}}'
                    )
                )
            )
        ]
    )
