"""Data-oriented helpers for canonical runtime trace adaptation.

The event adapter is intentionally focused on translating event shapes. These
helpers keep counters and history lookups separately named and easy to test.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from claim_agent.agent.runtime_trace import RuntimeTraceEntry

EVIDENCE_REASON_BY_ERROR_CODE = {
    "AMBIGUOUS_POLICY": "POLICY_NOT_APPLICABLE",
    "DOCUMENT_UNAVAILABLE": "MISSING_EVIDENCE",
    "MALFORMED_REQUEST": "MALFORMED_TOOL_REQUEST",
}


def build_runtime_counters(
    entries: Sequence[RuntimeTraceEntry],
) -> dict[str, int]:
    """Count the budgeted operations represented by one completed trace."""

    model_calls = sum(entry.event_name == "MODEL_REQUESTED" for entry in entries)
    tool_calls = sum(
        entry.event_name in {"TOOL_REQUESTED", "TOOL_RETRY_REQUESTED"}
        for entry in entries
    )
    repairs = sum(entry.event_name == "REPAIR_ATTEMPTED" for entry in entries)
    tool_retries = sum(
        entry.event_name == "TOOL_RETRY_REQUESTED" for entry in entries
    )

    return {
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "tool_retries": tool_retries,
        "repairs": repairs,
        "total_steps": model_calls + tool_calls,
    }


def next_model_call_index(
    prior_entries: Sequence[RuntimeTraceEntry],
) -> int:
    """Return the one-based index for the next recorded model request."""

    return 1 + sum(
        entry.event_name == "MODEL_REQUESTED" for entry in prior_entries
    )


def collected_passage_ids(
    prior_entries: Sequence[RuntimeTraceEntry],
) -> list[str]:
    """Return unique evidence passage IDs observed before the current event."""

    passage_ids: list[str] = []
    for entry in prior_entries:
        if entry.event_name != "TOOL_RETURNED":
            continue

        for passage_id in entry.details.get("passage_ids", []):
            normalized_passage_id = str(passage_id)
            if normalized_passage_id not in passage_ids:
                passage_ids.append(normalized_passage_id)

    return passage_ids


def build_validation_payload(
    details: Mapping[str, object],
) -> dict[str, object]:
    """Translate the internal validation status to the shared outcome codes."""

    outcome_by_status = {
        "ACCEPTED": "ACCEPTED",
        "REPAIRABLE": "REPAIRABLE",
        "MANUAL_REVIEW": "REJECTED_MANUAL_REVIEW",
    }

    return {
        "contract_version": "0.1",
        "decision_id": details["decision_id"],
        "claim_id": details["claim_id"],
        "service_line_id": details["service_line_id"],
        "outcome": outcome_by_status[str(details["status"])],
        "approval_state": "PENDING_REVIEW",
        "validation_problems": details["validation_problems"],
        "validated_decision": details["validated_decision"],
        "manual_review_reasons": details["manual_review_reasons"],
    }


def latest_validated_decision(
    prior_entries: Sequence[RuntimeTraceEntry],
) -> Mapping[str, object]:
    """Find the most recent validated decision before a terminal event."""

    for entry in reversed(prior_entries):
        if entry.event_name != "VALIDATION_RETURNED":
            continue

        decision = entry.details.get("validated_decision")
        if isinstance(decision, Mapping):
            return decision

    return {}


def build_manual_review_reasons(
    terminal_reason: str,
    prior_entries: Sequence[RuntimeTraceEntry],
) -> list[str]:
    """Collect the terminal, validator, and evidence reasons in stable order."""

    reasons = [terminal_reason]

    # Safety reasons come immediately after the terminal reason, even when a
    # tool error occurred earlier in chronological runtime history.
    for entry in prior_entries:
        if entry.event_name == "VALIDATION_RETURNED":
            reasons.extend(
                str(reason)
                for reason in entry.details.get("manual_review_reasons", [])
            )

    for entry in prior_entries:
        if entry.event_name != "TOOL_RETURNED":
            continue

        error_code = str(entry.details.get("error_code") or "")
        if error_code == "NO_MATCH":
            tool_name = str(entry.details.get("tool_name") or "")
            reasons.append(
                "POLICY_NOT_APPLICABLE"
                if tool_name == "retrieve_medical_policy"
                else "MISSING_EVIDENCE"
            )
        elif error_code in EVIDENCE_REASON_BY_ERROR_CODE:
            reasons.append(EVIDENCE_REASON_BY_ERROR_CODE[error_code])

    # Dictionary keys preserve insertion order and remove duplicates without
    # losing the required terminal-reason-first convention.
    return list(dict.fromkeys(reasons))
