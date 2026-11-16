"""Internal trace emission for tool-less verification."""

from __future__ import annotations

import hashlib
from typing import Mapping, Protocol

from claim_agent.agent.verification.interface import (
    VerifierRequest,
    VerifierResponse,
)
from claim_agent.agent.verification.prompt import VERIFIER_PROMPT_VERSION
from claim_agent.contracts import VerifierInput, VerifierValidationResult


class RuntimeTraceRecorder(Protocol):
    """Small trace surface required by verifier orchestration."""

    def record(
        self,
        event_name: str,
        details: Mapping[str, object] | None = None,
    ) -> object:
        """Record one internal event for canonical adaptation."""


def record_verifier_request(
    runtime_trace: RuntimeTraceRecorder | None,
    request: VerifierRequest,
    verifier_input: VerifierInput,
) -> None:
    """Record the exact prompt identity and evidence provenance."""

    prompt = request.system_instructions + "\n\n" + request.task_context
    _record(
        runtime_trace,
        "MODEL_REQUESTED",
        {
            "request_id": request.request_id,
            "model_role": "VERIFIER",
            "prompt_version": VERIFIER_PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(
                prompt.encode("utf-8")
            ).hexdigest(),
            "bundle_fingerprint": verifier_input.bundle_fingerprint,
            "evidence_run_id": verifier_input.run_id,
            "knowledge_snapshot_id": verifier_input.knowledge_snapshot_id,
            "is_repair": False,
        },
    )


def record_verifier_response(
    runtime_trace: RuntimeTraceRecorder | None,
    response: VerifierResponse,
    elapsed_ms: int,
) -> None:
    """Record provider output and audit metadata."""

    _record(
        runtime_trace,
        "MODEL_RESPONDED",
        {
            "request_id": response.request_id,
            "provider_request_id": response.provider_request_id or "",
            "model_role": "VERIFIER",
            "response_text": response.response_text,
            "input_tokens": response.input_token_count,
            "output_tokens": response.output_token_count,
            "elapsed_ms": elapsed_ms,
            "is_repair": False,
        },
    )


def record_verifier_failure(
    runtime_trace: RuntimeTraceRecorder | None,
    request_id: str,
    error: Exception,
    failure_class: str,
    elapsed_ms: int,
) -> None:
    """Record sanitized provider failure metadata."""

    _record(
        runtime_trace,
        "MODEL_FAILED",
        {
            "request_id": request_id,
            "model_role": "VERIFIER",
            "error_type": type(error).__name__,
            "failure_class": failure_class,
            "retryable": False,
            "elapsed_ms": elapsed_ms,
            "is_repair": False,
        },
    )


def record_verification_result(
    runtime_trace: RuntimeTraceRecorder | None,
    verifier_input: VerifierInput,
    validation_result: VerifierValidationResult,
    elapsed_ms: int,
) -> None:
    """Record the deterministic verifier outcome and provenance."""

    _record(
        runtime_trace,
        "VERIFICATION_RETURNED",
        {
            "decision_id": validation_result.decision_id,
            "claim_id": validation_result.claim_id,
            "service_line_id": validation_result.service_line_id,
            "outcome": validation_result.outcome.value,
            "reason_code": validation_result.reason_code,
            "requires_manual_review": validation_result.requires_manual_review,
            "evidence_run_id": verifier_input.run_id,
            "knowledge_snapshot_id": verifier_input.knowledge_snapshot_id,
            "bundle_fingerprint": verifier_input.bundle_fingerprint,
            "validation_problems": [
                problem.model_dump(mode="json")
                for problem in validation_result.validation_problems
            ],
            "elapsed_ms": elapsed_ms,
        },
    )


def _record(
    runtime_trace: RuntimeTraceRecorder | None,
    event_name: str,
    details: Mapping[str, object],
) -> None:
    """Record an event when orchestration supplied a trace sink."""

    if runtime_trace is not None:
        runtime_trace.record(event_name, details)
