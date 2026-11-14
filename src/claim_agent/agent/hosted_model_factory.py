"""Build hosted model clients from explicit profiles and local credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from claim_agent.agent.hosted_model import HostedModelClient
from claim_agent.agent.openai_chat_transport import (
    OpenAIChatTransportSettings,
    OpenAICompatibleChatTransport,
)


HOSTED_API_KEY_ENVIRONMENT_VARIABLE = "CLAIM_AGENT_LLM_API_KEY"
VOYAGER_PROVIDER_NAME = "asu-rc-voyager"
VOYAGER_BASE_URL = "https://openai.rc.asu.edu/v1"


class HostedModelConfigurationError(ValueError):
    """Report missing or invalid non-provider runtime setup."""


@dataclass(frozen=True)
class HostedModelProfile:
    """Frozen provider and model settings excluding credentials."""

    provider_name: str
    base_url: str
    model_id: str
    max_completion_tokens: int
    reasoning_effort: str
    temperature: float
    response_format_mode: str
    request_timeout_seconds: float
    disable_gateway_addons: bool = True

    def __post_init__(self) -> None:
        """Validate the profile before it can construct a client."""

        non_blank_values = {
            "provider_name": self.provider_name,
            "base_url": self.base_url,
            "model_id": self.model_id,
        }
        for field_name, value in non_blank_values.items():
            if not value.strip():
                raise ValueError(
                    f"{field_name} must contain a "
                    "non-whitespace value"
                )

        if self.request_timeout_seconds <= 0:
            raise ValueError(
                "request_timeout_seconds must be greater than zero"
            )

        self.to_transport_settings()

    def to_transport_settings(
        self,
    ) -> OpenAIChatTransportSettings:
        """Translate the profile into transport-only settings."""

        return OpenAIChatTransportSettings(
            base_url=self.base_url,
            max_completion_tokens=self.max_completion_tokens,
            reasoning_effort=self.reasoning_effort,
            temperature=self.temperature,
            response_format_mode=self.response_format_mode,
            disable_gateway_addons=self.disable_gateway_addons,
        )


PROVISIONAL_VOYAGER_PROFILE = HostedModelProfile(
    provider_name=VOYAGER_PROVIDER_NAME,
    base_url=VOYAGER_BASE_URL,
    model_id="mimo-v2-6-pro",
    max_completion_tokens=4096,
    reasoning_effort="low",
    temperature=0.0,
    response_format_mode="json_schema",
    request_timeout_seconds=30.0,
)


def build_hosted_model_client(
    *,
    profile: HostedModelProfile = PROVISIONAL_VOYAGER_PROFILE,
    environment: Mapping[str, str] | None = None,
) -> HostedModelClient:
    """Build one hosted client from a profile and local key."""

    environment_values = os.environ if environment is None else environment
    api_key = environment_values.get(
        HOSTED_API_KEY_ENVIRONMENT_VARIABLE,
        "",
    )

    if not api_key.strip():
        raise HostedModelConfigurationError(
            "Hosted execution requires the "
            f"{HOSTED_API_KEY_ENVIRONMENT_VARIABLE} "
            "environment variable."
        )

    transport = OpenAICompatibleChatTransport.from_api_key(
        api_key=api_key,
        settings=profile.to_transport_settings(),
    )

    return HostedModelClient(
        transport=transport,
        model_name=profile.model_id,
        timeout_seconds=profile.request_timeout_seconds,
    )
