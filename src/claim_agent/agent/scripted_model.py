"""Deterministic model implementation for offline runtime tests.

The scripted model returns responses in a predefined order. It allows the team
to test planning, repair, budgeting, and terminal behavior without making a
network request or depending on a hosted model's variable output.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Optional

from claim_agent.agent.model_interface import ModelRequest, ModelResponse


class ScriptedModelExhaustedError(RuntimeError):
    """Raised when the runtime requests more responses than the script contains."""


@dataclass(frozen=True)
class ScriptedModelOutput:
    """One predefined output that the scripted model will return.

    Attributes:
        response_text: Raw model text returned to the runtime.
        input_token_count: Optional simulated input-token count.
        output_token_count: Optional simulated output-token count.
    """

    response_text: str
    input_token_count: Optional[int] = None
    output_token_count: Optional[int] = None


class ScriptedModelClient:
    """Return predefined model outputs in first-in, first-out order.

    Each call consumes exactly one scripted output. The client also records all
    received requests so tests can verify what the runtime sent to the model.
    """

    def __init__(self, scripted_outputs: Iterable[ScriptedModelOutput]) -> None:
        """Store an independent queue of outputs for this model instance."""

        self._remaining_outputs = deque(scripted_outputs)
        self._received_requests: list[ModelRequest] = []

    @property
    def received_requests(self) -> tuple[ModelRequest, ...]:
        """Return an immutable view of every request received so far."""

        return tuple(self._received_requests)

    @property
    def remaining_output_count(self) -> int:
        """Report how many scripted outputs have not yet been consumed."""

        return len(self._remaining_outputs)

    def generate_response(self, request: ModelRequest) -> ModelResponse:
        """Return the next scripted output for the supplied request.

        The request identifier comes from the current request rather than from
        the script. This guarantees that every response can be correlated with
        the exact request that caused it.
        """

        if not self._remaining_outputs:
            raise ScriptedModelExhaustedError(
                "The scripted model has no response remaining for "
                f"request {request.request_id!r}."
            )

        self._received_requests.append(request)
        scripted_output = self._remaining_outputs.popleft()
        response_number = len(self._received_requests)

        return ModelResponse(
            request_id=request.request_id,
            response_text=scripted_output.response_text,
            input_token_count=scripted_output.input_token_count,
            output_token_count=scripted_output.output_token_count,
            provider_request_id=f"scripted-response-{response_number}",
        )
