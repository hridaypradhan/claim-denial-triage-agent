"""Internal trace emission for grounded appeal drafting."""

from __future__ import annotations

import hashlib
from typing import Mapping, Protocol

from claim_agent.agent.drafting.interface import (
    DraftingRequest,
    DraftingResponse,
)
from claim_agent.agent.drafting.prompt import DRAFTING_PROMPT_VERSION
from claim_agent.contracts import (
    DraftingInput,
    DraftingValidationResult,
)


class RuntimeTraceRecorder(Protocol):
    """Small trace surface required by drafting orchestration."""

    def record(
        self,
        event_name: str,
        details: Mapping[str, object] | None = None,
    ) -> object:
        """Record one internal event for canonical adaptation."""


def record_drafting_request(
    runtime_trace: RuntimeTraceRecorder,
    request: DraftingRequest,
    drafting_input: DraftingInput,
) -> None:
    """Record prompt identity and frozen evidence provenance."""

    prompt = request.system_instructions + "\n\n" + request.task_context
    runtime_trace.record(
        "MODEL_REQUESTED",
        {
            "request_id": request.request_id,
            "model_role": "DRAFTER",
            "prompt_version": DRAFTING_PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "bundle_fingerprint": drafting_input.bundle_fingerprint,
            "evidence_run_id": drafting_input.run_id,
            "knowledge_snapshot_id": (drafting_input.knowledge_snapshot_id),
            "is_repair": False,
        },
    )


def record_drafting_response(
    runtime_trace: RuntimeTraceRecorder,
    response: DraftingResponse,
    elapsed_ms: int,
) -> None:
    """Record provider output and audit metadata."""

    runtime_trace.record(
        "MODEL_RESPONDED",
        {
            "request_id": response.request_id,
            "provider_request_id": (response.provider_request_id or ""),
            "model_role": "DRAFTER",
            "response_text": response.response_text,
            "input_tokens": response.input_token_count,
            "output_tokens": response.output_token_count,
            "elapsed_ms": elapsed_ms,
            "is_repair": False,
        },
    )


def record_drafting_failure(
    runtime_trace: RuntimeTraceRecorder,
    request_id: str,
    error: Exception,
    failure_class: str,
    elapsed_ms: int,
) -> None:
    """Record sanitized provider failure metadata."""

    runtime_trace.record(
        "MODEL_FAILED",
        {
            "request_id": request_id,
            "model_role": "DRAFTER",
            "error_type": type(error).__name__,
            "failure_class": failure_class,
            "retryable": False,
            "elapsed_ms": elapsed_ms,
            "is_repair": False,
        },
    )


def record_drafting_result(
    runtime_trace: RuntimeTraceRecorder,
    drafting_input: DraftingInput,
    validation_result: DraftingValidationResult,
    elapsed_ms: int,
) -> None:
    """Record deterministic groundedness outcome and provenance."""

    validated_draft = validation_result.validated_draft
    runtime_trace.record(
        "DRAFTING_RETURNED",
        {
            "decision_id": validation_result.decision_id,
            "claim_id": validation_result.claim_id,
            "service_line_id": validation_result.service_line_id,
            "outcome": validation_result.outcome.value,
            "reason_code": validation_result.reason_code,
            "requires_manual_review": (validation_result.requires_manual_review),
            "approval_state": validation_result.approval_state,
            "citation_ids": (
                [] if validated_draft is None else list(validated_draft.citation_ids)
            ),
            "evidence_run_id": drafting_input.run_id,
            "knowledge_snapshot_id": (drafting_input.knowledge_snapshot_id),
            "bundle_fingerprint": drafting_input.bundle_fingerprint,
            "validation_problems": [
                problem.model_dump(mode="json")
                for problem in validation_result.validation_problems
            ],
            "elapsed_ms": elapsed_ms,
        },
    )
