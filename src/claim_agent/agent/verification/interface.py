"""Provider-independent boundary for tool-less evidence verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class VerifierRequest:
    """Prompt information sent to a tool-less evidence verifier.

    Unlike the planning model request, this type deliberately has no tool
    allowlist. A verifier receives only its instructions and frozen task
    context, making tool authority impossible to express at this boundary.
    """

    request_id: str
    system_instructions: str
    task_context: str

    def __post_init__(self) -> None:
        """Reject requests that cannot be identified or interpreted."""

        required_text_fields = {
            "request_id": self.request_id,
            "system_instructions": self.system_instructions,
            "task_context": self.task_context,
        }

        for field_name, value in required_text_fields.items():
            if not value.strip():
                raise ValueError(f"{field_name} must contain a non-whitespace value")


@dataclass(frozen=True)
class VerifierResponse:
    """Raw verifier output and optional provider audit metadata."""

    request_id: str
    response_text: str
    input_token_count: Optional[int] = None
    output_token_count: Optional[int] = None
    provider_request_id: Optional[str] = None

    def __post_init__(self) -> None:
        """Reject malformed output before deterministic validation."""

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
class VerifierClient(Protocol):
    """Behavior required from fake and hosted verifier clients."""

    def verify(self, request: VerifierRequest) -> VerifierResponse:
        """Return raw structured output for deterministic validation."""
