"""Build, execute, trace, and validate tool-less verification.

This module is the single execution boundary between an accepted proposed
decision, its frozen same-run evidence bundle, and a fake or hosted verifier.
Provider failures become explicit manual-review results rather than approvals
or silent fallback behavior.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from claim_agent.agent.verification.interface import (
    VerifierClient,
    VerifierRequest,
    VerifierResponse,
)
from claim_agent.agent.verification.prompt import build_verifier_request
from claim_agent.agent.verification.trace import (
    RuntimeTraceRecorder,
    record_verification_result,
    record_verifier_failure,
    record_verifier_request,
    record_verifier_response,
)
from claim_agent.contracts import (
    AgentDecision,
    EvidenceBundle,
    ValidationResult,
    VerifierInput,
    VerifierValidationResult,
)
from claim_agent.safety.verifier import validate_verifier_response


@dataclass(frozen=True)
class VerifierExecutionResult:
    """Observable values produced by one verifier attempt."""

    verifier_input: VerifierInput
    request: VerifierRequest
    response: VerifierResponse | None
    validation_result: VerifierValidationResult
    elapsed_ms: int


def build_verifier_input(
    *,
    proposed_decision: AgentDecision,
    evidence_bundle: EvidenceBundle,
) -> VerifierInput:
    """Bind one proposed decision to its frozen same-run evidence bundle."""

    return VerifierInput(
        proposed_decision=proposed_decision,
        evidence_bundle=evidence_bundle,
        authorized_tools=[],
    )


def execute_verification(
    *,
    verifier_client: VerifierClient,
    request_id: str,
    proposed_decision: AgentDecision,
    evidence_bundle: EvidenceBundle,
    deterministic_validation: ValidationResult,
    runtime_trace: RuntimeTraceRecorder,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> VerifierExecutionResult:
    """Run one verifier call and fail safely on boundary errors.

    Contract-construction errors are intentionally not caught because they are
    local integration errors occurring before a provider call. After entering
    the provider boundary, timeout and provider failures become typed
    manual-review results.

    The trace recorder is required so no caller can perform verification
    without preserving its request, response or failure, deterministic
    outcome, latency, and same-run evidence provenance.
    """

    verifier_input = build_verifier_input(
        proposed_decision=proposed_decision,
        evidence_bundle=evidence_bundle,
    )
    request = build_verifier_request(
        request_id=request_id,
        verifier_input=verifier_input,
    )

    record_verifier_request(
        runtime_trace,
        request,
        verifier_input,
    )

    started_at = monotonic_clock()
    response: VerifierResponse | None = None

    try:
        response = verifier_client.verify(request)
    except TimeoutError as error:
        elapsed_ms = _elapsed_milliseconds(
            started_at=started_at,
            monotonic_clock=monotonic_clock,
        )
        record_verifier_failure(
            runtime_trace,
            request.request_id,
            error,
            "REQUEST_TIMEOUT",
            elapsed_ms,
        )
        validation_result = validate_verifier_response(
            verifier_input,
            deterministic_validation,
            timed_out=True,
        )
    except Exception as error:
        elapsed_ms = _elapsed_milliseconds(
            started_at=started_at,
            monotonic_clock=monotonic_clock,
        )
        record_verifier_failure(
            runtime_trace,
            request.request_id,
            error,
            "BOUNDARY_EXCEPTION",
            elapsed_ms,
        )
        validation_result = validate_verifier_response(
            verifier_input,
            deterministic_validation,
            provider_error=type(error).__name__,
        )
    else:
        elapsed_ms = _elapsed_milliseconds(
            started_at=started_at,
            monotonic_clock=monotonic_clock,
        )
        record_verifier_response(
            runtime_trace,
            response,
            elapsed_ms,
        )

        if response.request_id != request.request_id:
            validation_result = validate_verifier_response(
                verifier_input,
                deterministic_validation,
                provider_error="VerifierResponseIdentityMismatch",
            )
        else:
            validation_result = validate_verifier_response(
                verifier_input,
                deterministic_validation,
                raw_output=response.response_text,
            )

    record_verification_result(
        runtime_trace,
        verifier_input,
        validation_result,
        elapsed_ms,
    )

    return VerifierExecutionResult(
        verifier_input=verifier_input,
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
