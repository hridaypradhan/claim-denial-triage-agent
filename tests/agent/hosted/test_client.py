"""Tests for the provider-neutral hosted model boundary."""

from __future__ import annotations

import unittest
from typing import Optional

from claim_agent.agent.hosted.client import (
    HostedModelClient,
    HostedModelTimeoutError,
    HostedModelTransport,
    HostedModelTransportRequest,
    HostedModelTransportResponse,
)
from claim_agent.agent.model_interface import (
    ModelClient,
    ModelRequest,
)

REQUEST_ID = "run-hosted:model:1"
MODEL_NAME = "test-hosted-model"


class RecordingHostedTransport:
    """Record one request and return a predefined response or error."""

    def __init__(
        self,
        response: Optional[HostedModelTransportResponse] = None,
        error: Optional[Exception] = None,
    ) -> None:
        """Store deterministic behavior for one offline test."""

        self.response = response
        self.error = error
        self.received_requests: list[HostedModelTransportRequest] = []

    def generate(
        self,
        request: HostedModelTransportRequest,
    ) -> HostedModelTransportResponse:
        """Record the request before returning or raising."""

        self.received_requests.append(request)

        if self.error is not None:
            raise self.error

        if self.response is None:
            raise AssertionError("The fake transport requires a response")

        return self.response


def make_model_request() -> ModelRequest:
    """Create one runtime request containing an allowlisted tool."""

    return ModelRequest(
        request_id=REQUEST_ID,
        system_instructions=("Use only retrieved evidence and return structured JSON."),
        task_context="Review the synthetic medical-necessity denial.",
        allowed_tool_names=("retrieve_medical_policy",),
    )


class HostedModelClientTests(unittest.TestCase):
    """Verify translation, timeout handling, and runtime compatibility."""

    def test_client_satisfies_runtime_model_interface(self) -> None:
        """The hosted adapter should replace the scripted model cleanly."""

        transport = RecordingHostedTransport(
            response=HostedModelTransportResponse(
                response_text='{"action":"request_manual_review"}'
            )
        )
        client = HostedModelClient(
            transport=transport,
            model_name=MODEL_NAME,
            timeout_seconds=15.0,
        )

        self.assertIsInstance(client, ModelClient)
        self.assertIsInstance(transport, HostedModelTransport)

    def test_request_and_response_are_translated(self) -> None:
        """Runtime fields and provider metadata should cross the boundary."""

        transport = RecordingHostedTransport(
            response=HostedModelTransportResponse(
                response_text=(
                    '{"action":"request_manual_review",'
                    '"reason":"Evidence is unavailable."}'
                ),
                provider_request_id="provider-request-123",
                input_token_count=120,
                output_token_count=18,
            )
        )
        client = HostedModelClient(
            transport=transport,
            model_name=MODEL_NAME,
            timeout_seconds=15.0,
        )

        response = client.generate_response(make_model_request())

        self.assertEqual(1, len(transport.received_requests))
        transport_request = transport.received_requests[0]

        self.assertEqual(REQUEST_ID, transport_request.request_id)
        self.assertEqual(MODEL_NAME, transport_request.model_name)
        self.assertEqual(
            ("retrieve_medical_policy",),
            transport_request.allowed_tool_names,
        )
        self.assertEqual(15.0, transport_request.timeout_seconds)

        self.assertEqual(REQUEST_ID, response.request_id)
        self.assertEqual(
            "provider-request-123",
            response.provider_request_id,
        )
        self.assertEqual(120, response.input_token_count)
        self.assertEqual(18, response.output_token_count)

    def test_transport_timeout_becomes_clear_boundary_error(self) -> None:
        """A provider timeout should retain runtime request context."""

        transport = RecordingHostedTransport(error=TimeoutError("Provider timed out"))
        client = HostedModelClient(
            transport=transport,
            model_name=MODEL_NAME,
            timeout_seconds=2.5,
        )

        with self.assertRaisesRegex(
            HostedModelTimeoutError,
            REQUEST_ID,
        ) as raised_context:
            client.generate_response(make_model_request())

        self.assertIsInstance(
            raised_context.exception.__cause__,
            TimeoutError,
        )

    def test_invalid_hosted_configuration_is_rejected(self) -> None:
        """Blank model names and nonpositive timeouts are unusable."""

        transport = RecordingHostedTransport()

        invalid_configurations = (
            {
                "model_name": " ",
                "timeout_seconds": 10.0,
                "message": "model_name",
            },
            {
                "model_name": MODEL_NAME,
                "timeout_seconds": 0.0,
                "message": "timeout_seconds",
            },
        )

        for configuration in invalid_configurations:
            with self.subTest(configuration=configuration):
                with self.assertRaisesRegex(
                    ValueError,
                    configuration["message"],
                ):
                    HostedModelClient(
                        transport=transport,
                        model_name=configuration["model_name"],
                        timeout_seconds=configuration["timeout_seconds"],
                    )

    def test_invalid_transport_response_is_rejected(self) -> None:
        """Blank provider output must not enter the action parser."""

        transport = RecordingHostedTransport(
            response=HostedModelTransportResponse(response_text=" ")
        )
        client = HostedModelClient(
            transport=transport,
            model_name=MODEL_NAME,
            timeout_seconds=15.0,
        )

        with self.assertRaisesRegex(ValueError, "response_text"):
            client.generate_response(make_model_request())


if __name__ == "__main__":
    unittest.main()
