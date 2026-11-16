"""Authorize, execute, trace, and validate grounded appeal drafting.

This module is the single execution boundary between an authorized
DraftingInput and a fake or hosted drafting client. Authorization is checked
before constructing a request or calling the provider. Every provider or
validation failure closes safely without producing a draft.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from claim_agent.agent.drafting.interface import (
    DraftingClient,
    DraftingRequest,
    DraftingResponse,
)
from claim_agent.agent.drafting.prompt import build_drafting_request
from claim_agent.agent.drafting.trace import (
    RuntimeTraceRecorder,
    record_drafting_failure,
    record_drafting_request,
    record_drafting_response,
    record_drafting_result,
)
from claim_agent.contracts import (
    DraftingInput,
    DraftingValidationResult,
)
from claim_agent.safety.drafting import (
    check_drafting_authorization,
    validate_appeal_draft,
)


@dataclass(frozen=True)
class DraftingExecutionResult:
    """Observable values produced by one drafting attempt."""

    drafting_input: DraftingInput
    request: DraftingRequest | None
    response: DraftingResponse | None
    validation_result: DraftingValidationResult
    elapsed_ms: int


def execute_drafting(
    *,
    drafting_client: DraftingClient,
    request_id: str,
    drafting_input: DraftingInput,
    runtime_trace: RuntimeTraceRecorder,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> DraftingExecutionResult:
    """Run one authorization-first, fully audited drafting attempt."""

    authorization_result = check_drafting_authorization(drafting_input)
    if authorization_result is not None:
        record_drafting_result(
            runtime_trace,
            drafting_input,
            authorization_result,
            0,
        )
        return DraftingExecutionResult(
            drafting_input=drafting_input,
            request=None,
            response=None,
            validation_result=authorization_result,
            elapsed_ms=0,
        )

    request = build_drafting_request(
        request_id=request_id,
        drafting_input=drafting_input,
    )
    record_drafting_request(
        runtime_trace,
        request,
        drafting_input,
    )

    started_at = monotonic_clock()
    response: DraftingResponse | None = None

    try:
        response = drafting_client.draft(request)
    except TimeoutError as error:
        elapsed_ms = _elapsed_milliseconds(
            started_at=started_at,
            monotonic_clock=monotonic_clock,
        )
        record_drafting_failure(
            runtime_trace,
            request.request_id,
            error,
            "REQUEST_TIMEOUT",
            elapsed_ms,
        )
        validation_result = validate_appeal_draft(
            drafting_input,
            timed_out=True,
        )
    except Exception as error:
        elapsed_ms = _elapsed_milliseconds(
            started_at=started_at,
            monotonic_clock=monotonic_clock,
        )
        record_drafting_failure(
            runtime_trace,
            request.request_id,
            error,
            "BOUNDARY_EXCEPTION",
            elapsed_ms,
        )
        validation_result = validate_appeal_draft(
            drafting_input,
            provider_error=type(error).__name__,
        )
    else:
        elapsed_ms = _elapsed_milliseconds(
            started_at=started_at,
            monotonic_clock=monotonic_clock,
        )
        record_drafting_response(
            runtime_trace,
            response,
            elapsed_ms,
        )

        if response.request_id != request.request_id:
            validation_result = validate_appeal_draft(
                drafting_input,
                provider_error="DraftingResponseIdentityMismatch",
            )
        else:
            validation_result = validate_appeal_draft(
                drafting_input,
                raw_output=response.response_text,
            )

    record_drafting_result(
        runtime_trace,
        drafting_input,
        validation_result,
        elapsed_ms,
    )

    return DraftingExecutionResult(
        drafting_input=drafting_input,
        request=request,
        response=response,
        validation_result=validation_result,
        elapsed_ms=elapsed_ms,
    )


def _elapsed_milliseconds(
    *,
    started_at: float,
    monotonic_clock: Callable[[], float],
) -> int:
    """Return nonnegative elapsed time rounded to whole milliseconds."""

    return max(
        0,
        round((monotonic_clock() - started_at) * 1000),
    )
