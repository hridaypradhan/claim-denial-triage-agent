"""Connect the runtime model interface to a replaceable hosted transport.

This module contains no provider SDK imports. A provider-specific transport can
translate the request into OpenAI, Azure, or another hosted API without making
the bounded runtime depend directly on that provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from claim_agent.agent.model_interface import (
    ModelClient,
    ModelRequest,
    ModelResponse,
)

# TODO(#11, owner=Hriday): Implement one provider-specific transport after
# the team selects the Milestone 2 hosted model and credential convention.


class HostedModelTimeoutError(TimeoutError):
    """Raised when a hosted model call exceeds its configured timeout."""


@dataclass(frozen=True)
class HostedModelTransportRequest:
    """Provider-neutral information required for one hosted model call."""

    request_id: str
    model_name: str
    system_instructions: str
    task_context: str
    allowed_tool_names: tuple[str, ...]
    timeout_seconds: float


@dataclass(frozen=True)
class HostedModelTransportResponse:
    """Provider-neutral values returned by a hosted model transport."""

    response_text: str
    provider_request_id: Optional[str] = None
    input_token_count: Optional[int] = None
    output_token_count: Optional[int] = None


@runtime_checkable
class HostedModelTransport(Protocol):
    """Behavior required from an OpenAI, Azure, or other provider adapter."""

    def generate(
        self,
        request: HostedModelTransportRequest,
    ) -> HostedModelTransportResponse:
        """Execute one hosted call while honoring the supplied timeout."""


class HostedModelClient:
    """Adapt a hosted transport to the runtime's stable model interface."""

    def __init__(
        self,
        transport: HostedModelTransport,
        model_name: str,
        timeout_seconds: float,
    ) -> None:
        """Store the transport and validated per-call configuration."""

        if not model_name.strip():
            raise ValueError("model_name must contain a non-whitespace value")

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        self._transport = transport
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds

    def generate_response(self, request: ModelRequest) -> ModelResponse:
        """Call the hosted transport and restore the runtime request identity."""

        transport_request = HostedModelTransportRequest(
            request_id=request.request_id,
            model_name=self._model_name,
            system_instructions=request.system_instructions,
            task_context=request.task_context,
            allowed_tool_names=request.allowed_tool_names,
            timeout_seconds=self._timeout_seconds,
        )

        try:
            transport_response = self._transport.generate(transport_request)
        except TimeoutError as error:
            raise HostedModelTimeoutError(
                "Hosted model request "
                f"{request.request_id} exceeded the "
                f"{self._timeout_seconds}-second timeout."
            ) from error

        return ModelResponse(
            request_id=request.request_id,
            response_text=transport_response.response_text,
            input_token_count=transport_response.input_token_count,
            output_token_count=transport_response.output_token_count,
            provider_request_id=transport_response.provider_request_id,
        )


# Static structural check for readers and type checkers: the adapter implements
# the same small boundary used by the deterministic scripted model.
_hosted_model_client_contract: type[ModelClient] = HostedModelClient
