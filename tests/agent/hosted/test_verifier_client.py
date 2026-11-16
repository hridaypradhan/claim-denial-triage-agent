"""Tests for the provider-neutral hosted verifier boundary."""

from __future__ import annotations

import unittest

from claim_agent.agent.hosted.client import (
    HostedModelTimeoutError,
    HostedModelTransport,
    HostedModelTransportRequest,
    HostedModelTransportResponse,
    HostedResponseSchema,
)
from claim_agent.agent.hosted.verifier_client import HostedVerifierClient
from claim_agent.agent.verification.interface import (
    VerifierClient,
    VerifierRequest,
)

REQUEST_ID = "run-verifier:verification:1"
MODEL_NAME = "test-verifier-model"


class RecordingVerifierTransport:
    """Record verifier requests and return deterministic offline responses."""

    def __init__(
        self,
        *,
        response: HostedModelTransportResponse | None = None,
        error: Exception | None = None,
    ) -> None:
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


def make_verifier_request() -> VerifierRequest:
    """Create one tool-less verifier request."""

    return VerifierRequest(
        request_id=REQUEST_ID,
        system_instructions=(
            "Verify the proposed decision using only the supplied evidence."
        ),
        task_context=(
            "The proposed decision and same-run evidence bundle are supplied."
        ),
    )


class HostedVerifierClientTests(unittest.TestCase):
    """Verify the distinct, tool-less hosted verifier boundary."""

    def test_client_satisfies_verifier_interface(self) -> None:
        """Hosted and fake verifier implementations should be replaceable."""

        transport = RecordingVerifierTransport(
            response=HostedModelTransportResponse(
                response_text='{"outcome":"UNCERTAIN"}'
            )
        )
        client = HostedVerifierClient(
            transport=transport,
            model_name=MODEL_NAME,
            timeout_seconds=15.0,
        )

        self.assertIsInstance(client, VerifierClient)
        self.assertIsInstance(transport, HostedModelTransport)

    def test_request_is_structurally_toolless(self) -> None:
        """The verifier client must always expose zero tool authority."""

        transport = RecordingVerifierTransport(
            response=HostedModelTransportResponse(
                response_text='{"outcome":"UNCERTAIN"}'
            )
        )
        client = HostedVerifierClient(
            transport=transport,
            model_name=MODEL_NAME,
            timeout_seconds=15.0,
        )

        client.verify(make_verifier_request())

        self.assertEqual(1, len(transport.received_requests))
        transport_request = transport.received_requests[0]

        self.assertEqual((), transport_request.allowed_tool_names)
        self.assertEqual(
            HostedResponseSchema.VERIFIER_REPORT,
            transport_request.response_schema,
        )

    def test_response_metadata_crosses_boundary(self) -> None:
        """Provider output and audit metadata should be preserved."""

        transport = RecordingVerifierTransport(
            response=HostedModelTransportResponse(
                response_text=('{"decision_id":"decision-1",' '"outcome":"UNCERTAIN"}'),
                provider_request_id="provider-verifier-123",
                input_token_count=240,
                output_token_count=32,
            )
        )
        client = HostedVerifierClient(
            transport=transport,
            model_name=MODEL_NAME,
            timeout_seconds=15.0,
        )

        response = client.verify(make_verifier_request())

        self.assertEqual(REQUEST_ID, response.request_id)
        self.assertEqual(
            "provider-verifier-123",
            response.provider_request_id,
        )
        self.assertEqual(240, response.input_token_count)
        self.assertEqual(32, response.output_token_count)

    def test_timeout_becomes_clear_boundary_error(self) -> None:
        """A verifier timeout should retain its request identity."""

        transport = RecordingVerifierTransport(error=TimeoutError("Provider timed out"))
        client = HostedVerifierClient(
            transport=transport,
            model_name=MODEL_NAME,
            timeout_seconds=2.5,
        )

        with self.assertRaisesRegex(
            HostedModelTimeoutError,
            REQUEST_ID,
        ):
            client.verify(make_verifier_request())


if __name__ == "__main__":
    unittest.main()
