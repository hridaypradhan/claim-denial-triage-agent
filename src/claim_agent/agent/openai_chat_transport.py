"""Call an OpenAI-compatible Chat Completions endpoint.

The transport translates the project's provider-neutral hosted-model request
into the OpenAI Chat Completions protocol. Provider selection, model selection,
and credentials remain configuration rather than runtime or model-specific
code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from openai import APITimeoutError, OpenAI

from claim_agent.agent.hosted_model import (
    HostedModelTransportRequest,
    HostedModelTransportResponse,
)
from claim_agent.agent.runtime_action_schema import (
    build_runtime_action_response_format,
)


class OpenAICompatibleProviderError(RuntimeError):
    """Report a sanitized failure at the hosted-provider boundary."""


class OpenAIChatCompletionsClient(Protocol):
    """Small SDK surface consumed by the transport."""

    chat: Any


@dataclass(frozen=True)
class OpenAIChatTransportSettings:
    """Frozen non-secret settings for an OpenAI-compatible endpoint."""

    base_url: str
    max_completion_tokens: int
    reasoning_effort: str
    temperature: float
    disable_gateway_addons: bool = True

    def __post_init__(self) -> None:
        """Reject settings that cannot produce a bounded request."""

        if not self.base_url.strip():
            raise ValueError("base_url must contain a non-whitespace value")

        if self.max_completion_tokens <= 0:
            raise ValueError(
                "max_completion_tokens must be greater than zero"
            )

        allowed_reasoning_efforts = {
            "none",
            "low",
            "medium",
            "high",
        }
        if self.reasoning_effort not in allowed_reasoning_efforts:
            raise ValueError(
                "reasoning_effort must be one of: "
                "none, low, medium, high"
            )

        if self.temperature < 0:
            raise ValueError("temperature cannot be negative")


class OpenAICompatibleChatTransport:
    """Execute one request through an OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        client: OpenAIChatCompletionsClient,
        settings: OpenAIChatTransportSettings,
    ) -> None:
        """Store an injected SDK client and non-secret request settings."""

        self._client = client
        self._settings = settings

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        settings: OpenAIChatTransportSettings,
    ) -> "OpenAICompatibleChatTransport":
        """Create the production SDK client without retaining the API key."""

        if not api_key.strip():
            raise ValueError(
                "api_key must contain a non-whitespace value"
            )

        client = OpenAI(
            api_key=api_key,
            base_url=settings.base_url,
            max_retries=0,
        )
        return cls(
            client=client,
            settings=settings,
        )

    def generate(
        self,
        request: HostedModelTransportRequest,
    ) -> HostedModelTransportResponse:
        """Translate one hosted request and normalize its response."""

        request_arguments: dict[str, object] = {
            "model": request.model_name,
            "messages": self._build_messages(request),
            "max_completion_tokens": (
                self._settings.max_completion_tokens
            ),
            "reasoning_effort": self._settings.reasoning_effort,
            "temperature": self._settings.temperature,
            "response_format": build_runtime_action_response_format(
                request.allowed_tool_names
            ),
            "timeout": request.timeout_seconds,
            "extra_headers": self._build_headers(request),
        }

        try:
            response = self._client.chat.completions.create(
                **request_arguments
            )
        except APITimeoutError as error:
            raise TimeoutError(
                "The OpenAI-compatible provider timed out."
            ) from error
        except TimeoutError:
            raise
        except Exception as error:
            raise OpenAICompatibleProviderError(
                "The OpenAI-compatible provider raised "
                f"{type(error).__name__}."
            ) from error

        response_text = self._extract_response_text(response)
        provider_request_id = self._extract_request_id(response)
        input_token_count, output_token_count = (
            self._extract_token_usage(response)
        )

        return HostedModelTransportResponse(
            response_text=response_text,
            provider_request_id=provider_request_id,
            input_token_count=input_token_count,
            output_token_count=output_token_count,
        )

    def _build_messages(
        self,
        request: HostedModelTransportRequest,
    ) -> list[dict[str, str]]:
        """Preserve the exact prompt text already hashed by the runtime.

        The deterministic case context contains the route's authorized tools,
        and the local runtime enforces the allowlist before execution. The
        transport deliberately sends no provider-hosted ``tools`` and does not
        append hidden instructions after the prompt hash has been recorded.
        """

        return [
            {
                "role": "system",
                "content": request.system_instructions,
            },
            {
                "role": "user",
                "content": request.task_context,
            },
        ]

    def _build_headers(
        self,
        request: HostedModelTransportRequest,
    ) -> dict[str, str]:
        """Return reproducibility and gateway-control request headers."""

        headers = {
            "X-Client-Request-Id": request.request_id,
        }
        if self._settings.disable_gateway_addons:
            headers["x-obleth-boons"] = "off"

        return headers

    @staticmethod
    def _extract_response_text(response: object) -> str:
        """Return one non-empty assistant response."""

        choices = getattr(response, "choices", None)
        if not choices:
            raise OpenAICompatibleProviderError(
                "The provider response did not contain a choice."
            )

        message = getattr(choices[0], "message", None)
        response_text = getattr(message, "content", None)

        if not isinstance(response_text, str) or not response_text.strip():
            raise OpenAICompatibleProviderError(
                "The provider response did not contain assistant text."
            )

        return response_text

    @staticmethod
    def _extract_request_id(response: object) -> str | None:
        """Prefer the HTTP request ID and fall back to the response ID."""

        request_id = getattr(response, "_request_id", None)
        if isinstance(request_id, str) and request_id.strip():
            return request_id

        response_id = getattr(response, "id", None)
        if isinstance(response_id, str) and response_id.strip():
            return response_id

        return None

    @staticmethod
    def _extract_token_usage(
        response: object,
    ) -> tuple[int | None, int | None]:
        """Return provider-reported input and output token counts."""

        usage = getattr(response, "usage", None)
        if usage is None:
            return None, None

        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)

        return (
            input_tokens if isinstance(input_tokens, int) else None,
            output_tokens if isinstance(output_tokens, int) else None,
        )
