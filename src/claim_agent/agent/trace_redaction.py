"""Default-deny redaction for evaluation-facing trace payloads."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping

REDACTED_VALUE = "[REDACTED]"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

CLAIM_KEYS = frozenset(
    {
        "case_id",
        "claim_id",
        "service_line_id",
        "payer",
        "payer_id",
        "date_of_service",
        "service_date",
        "cpt",
        "procedure_code",
        "modifiers",
        "icd10",
        "group_code",
        "carc",
        "rarc",
    }
)

IDENTIFIER_KEYS = frozenset(
    {
        "knowledge_snapshot_id",
        "document_id",
        "policy_id",
        "source_ids",
        "passage_ids",
        "citation_ids",
        "criterion_id",
        "decision_id",
        "request_id",
        "rejected_request_id",
        "retry_of_request_id",
        "run_id",
        "experiment_id",
        "authorized_tools",
    }
)

CONTROLLED_VALUE_KEYS = frozenset(
    {
        "event_type",
        "component",
        "status",
        "outcome",
        "reason_code",
        "error_code",
        "manual_review_reasons",
        "evidence_gaps",
        "root_cause",
        "recommended_disposition",
        "final_disposition",
        "final_root_cause",
        "agent_status",
        "response_type",
        "rejection_code",
        "schema_error_paths",
        "field_path",
        "repair_kind",
        "failure_class",
        "tool_name",
        "error_type",
        "approval_state",
        "contract_version",
        "usage_status",
        "terminal_reason",
    }
)

NUMERIC_AND_BOOLEAN_KEYS = frozenset(
    {
        "call_index",
        "repair_attempt",
        "repair_index",
        "tool_call_index",
        "attempt",
        "repeat_index",
        "elapsed_ms",
        "input_tokens",
        "output_tokens",
        "retryable",
        "is_repairable",
        "during_repair",
        "model_calls",
        "tool_calls",
        "tool_retries",
        "repairs",
        "total_steps",
        "max_output_tokens",
        "temperature",
        "top_p",
        "runtime_seed",
        "provider_seed",
        "provider_seed_supported",
        "max_model_calls",
        "max_tool_calls",
        "max_repair_attempts",
        "max_total_steps",
        "max_tool_retries",
        "max_runtime_seconds",
        "model_request_timeout_seconds",
        "tool_request_timeout_seconds",
    }
)

RUN_CONFIGURATION_KEYS = frozenset(
    {
        "config_version",
        "code_commit",
        "model_provider",
        "model_id",
        "model_parameters",
        "prompt_version",
        "allowed_tools",
        "tool_version",
    }
)

STRUCTURE_KEYS = frozenset(
    {
        "config",
        "payload",
        "route",
        "decision",
        "criterion_assessments",
        "validation_problems",
        "validated_decision",
        "tool_calls",
        "tool_args",
        "counters",
        "evaluation_context",
    }
)

FREE_TEXT_KEYS = frozenset(
    {
        "rationale",
        "explanation",
        "message",
        "routing_reason",
        "query",
        "raw_output",
        "actions",
    }
)

ALLOWED_VALUE_KEYS = (
    CLAIM_KEYS
    | IDENTIFIER_KEYS
    | CONTROLLED_VALUE_KEYS
    | NUMERIC_AND_BOOLEAN_KEYS
    | RUN_CONFIGURATION_KEYS
)


def calculate_redacted_value_sha256(value: object) -> str:
    """Return a stable fingerprint without retaining the original value."""

    if isinstance(value, str):
        serialized_value = value
    else:
        serialized_value = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    return hashlib.sha256(serialized_value.encode("utf-8")).hexdigest()


def redact_trace_payload(
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Return a recursively filtered copy safe for trace persistence."""

    redacted_payload: dict[str, object] = {}

    for key, value in payload.items():
        if key.endswith("_sha256"):
            redacted_payload[key] = (
                value
                if isinstance(value, str) and SHA256_PATTERN.fullmatch(value)
                else REDACTED_VALUE
            )
            continue

        if key in FREE_TEXT_KEYS:
            redacted_payload[key] = _redact_free_text_value(value)
            redacted_payload[f"{key}_sha256"] = calculate_redacted_value_sha256(value)
            continue

        if key in STRUCTURE_KEYS:
            if isinstance(value, (Mapping, list, tuple)):
                redacted_payload[key] = _redact_structure(value)
            elif key in ALLOWED_VALUE_KEYS:
                # ``tool_calls`` is both a numeric counter and a structured
                # model-response field. Only its numeric scalar form is safe.
                redacted_payload[key] = _copy_allowed_value(value)
            else:
                redacted_payload[key] = REDACTED_VALUE
            continue

        if key in ALLOWED_VALUE_KEYS:
            redacted_payload[key] = _copy_allowed_value(value)
            continue

        # Keeping the key shows reviewers that the field existed, while the
        # default-deny value prevents unapproved content from entering traces.
        redacted_payload[key] = REDACTED_VALUE

    return redacted_payload


def _redact_structure(value: object) -> object:
    """Recursively filter an approved container."""

    if isinstance(value, Mapping):
        return redact_trace_payload(value)

    if isinstance(value, (list, tuple)):
        return [
            _redact_structure(item)
            for item in value
        ]

    # A structure key authorizes a container shape, not arbitrary scalar
    # content placed directly inside that container.
    return REDACTED_VALUE


def _copy_allowed_value(value: object) -> object:
    """Copy an approved scalar or recursively copy its collection."""

    if isinstance(value, Mapping):
        return redact_trace_payload(value)

    if isinstance(value, (list, tuple)):
        return [_copy_allowed_value(item) for item in value]

    return value


def _redact_free_text_value(value: object) -> object:
    """Preserve collection shape while replacing every free-text value."""

    if isinstance(value, (list, tuple)):
        return [REDACTED_VALUE for _ in value]

    return REDACTED_VALUE
