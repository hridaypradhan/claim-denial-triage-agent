"""Tests for the provider-neutral hosted drafting boundary."""

from __future__ import annotations

import unittest

from claim_agent.agent.drafting.interface import DraftingClient, DraftingRequest
from claim_agent.agent.hosted.client import (
    HostedModelTimeoutError,
    HostedModelTransport,
    HostedModelTransportRequest,
    HostedModelTransportResponse,
    HostedResponseSchema,
)
from claim_agent.agent.hosted.drafting_client import HostedDraftingClient

REQUEST_ID = "run-drafting:draft:1"
MODEL_NAME = "test-drafting-model"


class RecordingDraftingTransport:
    """Record drafting requests and return deterministic offline responses."""

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
        self.received_requests.append(request)
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("The fake transport requires a response")
        return self.response


def make_drafting_request() -> DraftingRequest:
    return DraftingRequest(
        request_id=REQUEST_ID,
        system_instructions="Draft only from the supplied evidence.",
        task_context="The authorized drafting input is supplied.",
    )


class HostedDraftingClientTests(unittest.TestCase):
    """Verify the distinct, tool-less hosted drafting boundary."""

    def test_client_satisfies_drafting_interface(self) -> None:
        transport = RecordingDraftingTransport(
            response=HostedModelTransportResponse(response_text="{}")
        )
        client = HostedDraftingClient(
            transport=transport,
            model_name=MODEL_NAME,
            timeout_seconds=15.0,
        )

        self.assertIsInstance(client, DraftingClient)
        self.assertIsInstance(transport, HostedModelTransport)

    def test_request_is_toolless_and_uses_appeal_draft_schema(self) -> None:
        transport = RecordingDraftingTransport(
            response=HostedModelTransportResponse(response_text="{}")
        )
        client = HostedDraftingClient(
            transport=transport,
            model_name=MODEL_NAME,
            timeout_seconds=15.0,
        )

        client.draft(make_drafting_request())
        transport_request = transport.received_requests[0]

        self.assertEqual((), transport_request.allowed_tool_names)
        self.assertEqual(
            HostedResponseSchema.APPEAL_DRAFT,
            transport_request.response_schema,
        )

    def test_response_metadata_crosses_boundary(self) -> None:
        transport = RecordingDraftingTransport(
            response=HostedModelTransportResponse(
                response_text='{"approval_state":"PENDING_REVIEW"}',
                provider_request_id="provider-draft-123",
                input_token_count=300,
                output_token_count=90,
            )
        )
        client = HostedDraftingClient(
            transport=transport,
            model_name=MODEL_NAME,
            timeout_seconds=15.0,
        )

        response = client.draft(make_drafting_request())

        self.assertEqual(REQUEST_ID, response.request_id)
        self.assertEqual("provider-draft-123", response.provider_request_id)
        self.assertEqual(300, response.input_token_count)
        self.assertEqual(90, response.output_token_count)

    def test_timeout_becomes_clear_boundary_error(self) -> None:
        transport = RecordingDraftingTransport(error=TimeoutError("timed out"))
        client = HostedDraftingClient(
            transport=transport,
            model_name=MODEL_NAME,
            timeout_seconds=2.5,
        )

        with self.assertRaisesRegex(HostedModelTimeoutError, REQUEST_ID):
            client.draft(make_drafting_request())


if __name__ == "__main__":
    unittest.main()
