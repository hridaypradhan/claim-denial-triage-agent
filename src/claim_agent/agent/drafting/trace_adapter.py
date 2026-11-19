"""Drafting-specific helpers for canonical trace adaptation."""

from __future__ import annotations

from typing import Mapping

from pydantic import ValidationError

from claim_agent.contracts import AppealDraft


def adapt_drafting_response(
    *,
    response_text: str,
    provider_request_id: str,
) -> dict[str, object]:
    """Return drafting audit fields without retaining appeal prose."""

    try:
        draft = AppealDraft.model_validate_json(response_text)
    except ValidationError:
        return {
            "model_role": "DRAFTER",
            "response_type": "INVALID",
            "raw_output": response_text,
            "provider_request_id": provider_request_id,
        }

    return {
        "model_role": "DRAFTER",
        "response_type": "APPEAL_DRAFT",
        "provider_request_id": provider_request_id,
        "contract_version": draft.contract_version,
        "draft_id": draft.draft_id,
        "decision_id": draft.decision_id,
        "claim_id": draft.claim_id,
        "service_line_id": draft.service_line_id,
        "disposition": draft.disposition.value,
        "approval_state": draft.approval_state,
        "citation_ids": list(draft.citation_ids),
    }


def build_drafting_payload(
    details: Mapping[str, object],
) -> dict[str, object]:
    """Shape one drafting result for canonical validation output."""

    return {
        "model_role": "DRAFTER",
        "decision_id": details["decision_id"],
        "claim_id": details["claim_id"],
        "service_line_id": details["service_line_id"],
        "outcome": details["outcome"],
        "reason_code": details["reason_code"],
        "requires_manual_review": details["requires_manual_review"],
        "approval_state": details["approval_state"],
        "citation_ids": details["citation_ids"],
        "evidence_run_id": details["evidence_run_id"],
        "knowledge_snapshot_id": details["knowledge_snapshot_id"],
        "bundle_fingerprint": details["bundle_fingerprint"],
        "validation_problems": details["validation_problems"],
        "elapsed_ms": details["elapsed_ms"],
    }
