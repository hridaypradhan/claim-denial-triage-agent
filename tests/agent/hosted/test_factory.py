"""Tests for hosted model profile and credential construction."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from claim_agent.agent.hosted.client import HostedModelClient
from claim_agent.agent.hosted.factory import (
    HOSTED_API_KEY_ENVIRONMENT_VARIABLE,
    PROVISIONAL_VOYAGER_PROFILE,
    VOYAGER_BASE_URL,
    VOYAGER_PROVIDER_NAME,
    HostedModelConfigurationError,
    HostedModelProfile,
    build_hosted_model_client,
)
from claim_agent.agent.hosted.openai_chat import (
    OpenAICompatibleChatTransport,
)


class HostedModelFactoryTests(unittest.TestCase):
    """Verify frozen settings and local credential handling."""

    def test_provisional_profile_records_voyager_settings(
        self,
    ) -> None:
        """The initial profile should be explicit and reproducible."""

        profile = PROVISIONAL_VOYAGER_PROFILE

        self.assertEqual(
            VOYAGER_PROVIDER_NAME,
            profile.provider_name,
        )
        self.assertEqual(
            VOYAGER_BASE_URL,
            profile.base_url,
        )
        self.assertEqual(
            "mimo-v2-6-pro",
            profile.model_id,
        )
        self.assertEqual(
            "json_schema",
            profile.response_format_mode,
        )
        self.assertEqual(
            "low",
            profile.reasoning_effort,
        )
        self.assertEqual(
            0.0,
            profile.temperature,
        )
        self.assertIs(
            True,
            profile.disable_gateway_addons,
        )

    def test_profile_translates_to_transport_settings(
        self,
    ) -> None:
        """Transport settings should preserve non-secret values."""

        settings = PROVISIONAL_VOYAGER_PROFILE.to_transport_settings()

        self.assertEqual(
            VOYAGER_BASE_URL,
            settings.base_url,
        )
        self.assertEqual(
            4096,
            settings.max_completion_tokens,
        )
        self.assertEqual(
            "json_schema",
            settings.response_format_mode,
        )
        self.assertIs(
            True,
            settings.disable_gateway_addons,
        )

    def test_missing_or_blank_key_is_rejected(
        self,
    ) -> None:
        """Hosted execution cannot begin without a local key."""

        invalid_environments = (
            {},
            {
                HOSTED_API_KEY_ENVIRONMENT_VARIABLE: "   ",
            },
        )

        for environment in invalid_environments:
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(
                    HostedModelConfigurationError,
                    HOSTED_API_KEY_ENVIRONMENT_VARIABLE,
                ):
                    build_hosted_model_client(environment=environment)

    def test_valid_key_builds_hosted_client(
        self,
    ) -> None:
        """The key should be passed only to the SDK factory."""

        fake_transport = object()

        with patch.object(
            OpenAICompatibleChatTransport,
            "from_api_key",
            return_value=fake_transport,
        ) as transport_factory:
            client = build_hosted_model_client(
                environment={
                    HOSTED_API_KEY_ENVIRONMENT_VARIABLE: (
                        "synthetic-voyager-key"
                    ),
                }
            )

        self.assertIsInstance(
            client,
            HostedModelClient,
        )
        transport_factory.assert_called_once()

        call_arguments = transport_factory.call_args
        self.assertEqual(
            "synthetic-voyager-key",
            call_arguments.kwargs["api_key"],
        )
        self.assertEqual(
            VOYAGER_BASE_URL,
            call_arguments.kwargs["settings"].base_url,
        )

    def test_missing_key_does_not_construct_sdk_client(
        self,
    ) -> None:
        """Credential failure should occur before SDK construction."""

        with patch.object(
            OpenAICompatibleChatTransport,
            "from_api_key",
        ) as transport_factory:
            with self.assertRaises(HostedModelConfigurationError):
                build_hosted_model_client(environment={})

        transport_factory.assert_not_called()

    def test_invalid_profile_is_rejected(self) -> None:
        """Profiles must be complete and bounded."""

        with self.assertRaisesRegex(
            ValueError,
            "model_id",
        ):
            HostedModelProfile(
                provider_name=VOYAGER_PROVIDER_NAME,
                base_url=VOYAGER_BASE_URL,
                model_id=" ",
                max_completion_tokens=4096,
                reasoning_effort="low",
                temperature=0.0,
                response_format_mode="json_schema",
                request_timeout_seconds=30.0,
            )


if __name__ == "__main__":
    unittest.main()
