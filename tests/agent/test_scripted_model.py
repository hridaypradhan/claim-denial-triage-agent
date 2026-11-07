"""Tests for the deterministic scripted model."""

from __future__ import annotations

import unittest

from claim_agent.agent.model_interface import ModelClient, ModelRequest
from claim_agent.agent.scripted_model import (
    ScriptedModelClient,
    ScriptedModelExhaustedError,
    ScriptedModelOutput,
)


def make_test_request(request_id: str) -> ModelRequest:
    """Create a concise model request shared by these tests."""

    return ModelRequest(
        request_id=request_id,
        system_instructions="Return the next structured agent action.",
        task_context="Review the current synthetic claim state.",
        allowed_tool_names=("retrieve_medical_policy",),
    )


class ScriptedModelClientTests(unittest.TestCase):
    """Verify deterministic output order, history, and exhaustion behavior."""

    def test_scripted_model_satisfies_model_client_protocol(self) -> None:
        """The fake model should be usable anywhere ModelClient is expected."""

        model_client = ScriptedModelClient(
            [ScriptedModelOutput(response_text='{"action": "finish"}')]
        )

        self.assertIsInstance(model_client, ModelClient)

    def test_scripted_outputs_are_returned_in_order(self) -> None:
        """Successive model calls should consume successive script entries."""

        model_client = ScriptedModelClient(
            [
                ScriptedModelOutput(
                    response_text='{"action": "call_tool"}',
                    input_token_count=10,
                    output_token_count=3,
                ),
                ScriptedModelOutput(
                    response_text='{"action": "finish"}',
                    input_token_count=14,
                    output_token_count=5,
                ),
            ]
        )

        first_response = model_client.generate_response(make_test_request("request-1"))
        second_response = model_client.generate_response(make_test_request("request-2"))

        self.assertEqual(
            first_response.response_text,
            '{"action": "call_tool"}',
        )
        self.assertEqual(
            second_response.response_text,
            '{"action": "finish"}',
        )
        self.assertEqual(first_response.request_id, "request-1")
        self.assertEqual(second_response.request_id, "request-2")
        self.assertEqual(model_client.remaining_output_count, 0)

    def test_received_requests_are_recorded_in_call_order(self) -> None:
        """Recorded history should show exactly what the runtime requested."""

        model_client = ScriptedModelClient(
            [
                ScriptedModelOutput(response_text='{"action": "continue"}'),
                ScriptedModelOutput(response_text='{"action": "finish"}'),
            ]
        )
        first_request = make_test_request("request-1")
        second_request = make_test_request("request-2")

        model_client.generate_response(first_request)
        model_client.generate_response(second_request)

        self.assertEqual(
            model_client.received_requests,
            (first_request, second_request),
        )

    def test_response_metadata_is_deterministic(self) -> None:
        """Provider request IDs should be predictable in offline tests."""

        model_client = ScriptedModelClient(
            [ScriptedModelOutput(response_text='{"action": "finish"}')]
        )

        response = model_client.generate_response(make_test_request("request-1"))

        self.assertEqual(
            response.provider_request_id,
            "scripted-response-1",
        )

    def test_request_after_script_exhaustion_raises_clear_error(self) -> None:
        """An incomplete test script should fail instead of inventing output."""

        model_client = ScriptedModelClient(
            [ScriptedModelOutput(response_text='{"action": "finish"}')]
        )
        model_client.generate_response(make_test_request("request-1"))

        with self.assertRaisesRegex(
            ScriptedModelExhaustedError,
            "request-2",
        ):
            model_client.generate_response(make_test_request("request-2"))


if __name__ == "__main__":
    unittest.main()
