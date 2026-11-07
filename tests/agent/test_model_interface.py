"""Tests for the provider-independent model boundary."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from claim_agent.agent.model_interface import (
    ModelClient,
    ModelRequest,
    ModelResponse,
)


class RecordingModelClient:
    """Small test model that records the request it receives.

    This class intentionally does not inherit from ModelClient. Python protocols
    use structural typing, which means a class satisfies the interface when it
    provides the required method with the expected shape.
    """

    def __init__(self) -> None:
        self.received_requests: list[ModelRequest] = []

    def generate_response(self, request: ModelRequest) -> ModelResponse:
        """Record the request and return a predictable response."""

        self.received_requests.append(request)

        return ModelResponse(
            request_id=request.request_id,
            response_text='{"action": "finish"}',
            input_token_count=12,
            output_token_count=4,
            provider_request_id="test-provider-request-1",
        )


class ModelInterfaceTests(unittest.TestCase):
    """Verify the behavior shared by fake and hosted model clients."""

    def test_model_client_protocol_accepts_compatible_class(self) -> None:
        """A class with generate_response should satisfy ModelClient."""

        model_client = RecordingModelClient()

        self.assertIsInstance(model_client, ModelClient)

    def test_model_client_receives_request_and_returns_matching_response(
        self,
    ) -> None:
        """The response should preserve the request identifier."""

        model_client = RecordingModelClient()
        request = ModelRequest(
            request_id="request-1",
            system_instructions="Return a structured action.",
            task_context="Review the synthetic claim.",
            allowed_tool_names=("retrieve_medical_policy",),
        )

        response = model_client.generate_response(request)

        self.assertEqual(model_client.received_requests, [request])
        self.assertEqual(response.request_id, request.request_id)
        self.assertEqual(response.response_text, '{"action": "finish"}')
        self.assertEqual(response.input_token_count, 12)
        self.assertEqual(response.output_token_count, 4)

    def test_model_request_rejects_blank_identifier(self) -> None:
        """A blank identifier would prevent request-response correlation."""

        with self.assertRaisesRegex(ValueError, "request_id"):
            ModelRequest(
                request_id="   ",
                system_instructions="Return a structured action.",
                task_context="Review the synthetic claim.",
            )

    def test_model_response_rejects_negative_token_count(self) -> None:
        """Token counts cannot be negative in traces or cost calculations."""

        with self.assertRaisesRegex(ValueError, "token counts"):
            ModelResponse(
                request_id="request-1",
                response_text='{"action": "finish"}',
                input_token_count=-1,
            )

    def test_model_request_cannot_be_changed_after_creation(self) -> None:
        """An immutable request preserves exactly what was sent to a model."""

        request = ModelRequest(
            request_id="request-1",
            system_instructions="Return a structured action.",
            task_context="Review the synthetic claim.",
        )

        with self.assertRaises(FrozenInstanceError):
            request.task_context = "Changed after the model call."


if __name__ == "__main__":
    unittest.main()
