"""Connect the grounded-drafting interface to a hosted transport."""

from __future__ import annotations

from claim_agent.agent.drafting.interface import (
    DraftingClient,
    DraftingRequest,
    DraftingResponse,
)
from claim_agent.agent.hosted.client import (
    HostedModelTimeoutError,
    HostedModelTransport,
    HostedModelTransportRequest,
    HostedResponseSchema,
)


class HostedDraftingClient:
    """Execute drafting calls without exposing any tool authority."""

    def __init__(
        self,
        transport: HostedModelTransport,
        model_name: str,
        timeout_seconds: float,
    ) -> None:
        """Store the transport and validated drafting configuration."""

        if not model_name.strip():
            raise ValueError("model_name must contain a non-whitespace value")

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        self._transport = transport
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds

    def draft(self, request: DraftingRequest) -> DraftingResponse:
        """Execute one strict drafting call with an empty tool allowlist."""

        transport_request = HostedModelTransportRequest(
            request_id=request.request_id,
            model_name=self._model_name,
            system_instructions=request.system_instructions,
            task_context=request.task_context,
            allowed_tool_names=(),
            timeout_seconds=self._timeout_seconds,
            response_schema=HostedResponseSchema.APPEAL_DRAFT,
        )

        try:
            transport_response = self._transport.generate(transport_request)
        except TimeoutError as error:
            raise HostedModelTimeoutError(
                "Hosted drafting request "
                f"{request.request_id} exceeded the "
                f"{self._timeout_seconds}-second timeout."
            ) from error

        return DraftingResponse(
            request_id=request.request_id,
            response_text=transport_response.response_text,
            input_token_count=transport_response.input_token_count,
            output_token_count=transport_response.output_token_count,
            provider_request_id=transport_response.provider_request_id,
        )


_drafting_client_contract: type[DraftingClient] = HostedDraftingClient
