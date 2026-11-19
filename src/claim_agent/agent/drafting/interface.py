"""Provider-independent boundary for grounded appeal drafting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class DraftingRequest:
    """Frozen prompt information sent to an appeal-drafting model.

    This boundary deliberately provides no tool allowlist. The drafting model
    receives only the already-authorized DraftingInput serialized by the
    prompt builder.
    """

    request_id: str
    system_instructions: str
    task_context: str

    def __post_init__(self) -> None:
        """Reject incomplete requests before a provider call."""

        required_text_fields = {
            "request_id": self.request_id,
            "system_instructions": self.system_instructions,
            "task_context": self.task_context,
        }

        for field_name, value in required_text_fields.items():
            if not value.strip():
                raise ValueError(f"{field_name} must contain a non-whitespace value")


@dataclass(frozen=True)
class DraftingResponse:
    """Raw draft output and optional provider audit metadata."""

    request_id: str
    response_text: str
    input_token_count: Optional[int] = None
    output_token_count: Optional[int] = None
    provider_request_id: Optional[str] = None

    def __post_init__(self) -> None:
        """Reject malformed responses before groundedness validation."""

        if not self.request_id.strip():
            raise ValueError("request_id must contain a non-whitespace value")

        if not self.response_text.strip():
            raise ValueError("response_text must contain a non-whitespace value")

        token_counts = (
            self.input_token_count,
            self.output_token_count,
        )
        if any(count is not None and count < 0 for count in token_counts):
            raise ValueError("token counts cannot be negative")


@runtime_checkable
class DraftingClient(Protocol):
    """Behavior required from fake and hosted drafting clients."""

    def draft(self, request: DraftingRequest) -> DraftingResponse:
        """Return raw structured output for deterministic validation."""
