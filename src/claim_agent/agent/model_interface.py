"""Provider-independent input and output types for language models.

The agent runtime communicates with models through the small interface in this
module. Keeping the interface independent of a specific model provider allows
the same runtime to use a scripted fake model during offline tests and a hosted
model in later project phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ModelRequest:
    """Information the agent runtime sends for one model call.

    The fields describe the request in project terms rather than using a
    provider-specific message format. A future provider adapter can translate
    this request into the format required by its external API.

    Attributes:
        request_id: Identifier used to connect this request to its response and
            trace events.
        system_instructions: Rules that describe how the model must behave.
        task_context: Claim facts, observed evidence, and the current task.
        allowed_tool_names: Tool names the model may select during this call.
            An empty tuple means the call is not permitted to request tools.
    """

    request_id: str
    system_instructions: str
    task_context: str
    allowed_tool_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject requests that cannot be identified or interpreted."""

        if not self.request_id.strip():
            raise ValueError("request_id must contain a non-whitespace value")

        if not self.system_instructions.strip():
            raise ValueError("system_instructions must contain a non-whitespace value")

        if not self.task_context.strip():
            raise ValueError("task_context must contain a non-whitespace value")


@dataclass(frozen=True)
class ModelResponse:
    """Provider-independent result returned from one model call.

    The runtime initially receives the model's output as text. A later parser
    will convert that text into a proposed tool call or agent decision. Keeping
    parsing outside the model client gives fake and hosted models the same
    responsibilities.

    Token counts are optional because a fake model or some hosted providers may
    not report them.
    """

    request_id: str
    response_text: str
    input_token_count: int | None = None
    output_token_count: int | None = None
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        """Reject malformed response metadata before it reaches the runtime."""

        if not self.request_id.strip():
            raise ValueError("request_id must contain a non-whitespace value")

        if not self.response_text.strip():
            raise ValueError("response_text must contain a non-whitespace value")

        optional_token_counts = (
            self.input_token_count,
            self.output_token_count,
        )

        if any(
            token_count is not None and token_count < 0
            for token_count in optional_token_counts
        ):
            raise ValueError("token counts cannot be negative")


@runtime_checkable
class ModelClient(Protocol):
    """Behavior that every fake or hosted model client must provide."""

    def generate_response(self, request: ModelRequest) -> ModelResponse:
        """Generate one response for the supplied model request."""
