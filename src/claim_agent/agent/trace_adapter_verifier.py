"""Verifier-specific helpers for canonical trace adaptation.

Keeping verifier response parsing and provenance shaping here prevents the
general trace adapter from accumulating another model-specific contract.
"""

from __future__ import annotations

from typing import Mapping

from pydantic import ValidationError

from claim_agent.contracts import VerifierReport


def adapt_verifier_response(
    *,
    response_text: str,
    provider_request_id: str,
) -> dict[str, object]:
    """Return structured verifier output without exposing raw text."""

    try:
        report = VerifierReport.model_validate_json(response_text)
    except ValidationError:
        return {
            "model_role": "VERIFIER",
            "response_type": "INVALID",
            "raw_output": response_text,
            "provider_request_id": provider_request_id,
        }

    return {
        "model_role": "VERIFIER",
        "response_type": "VERIFIER_REPORT",
        "provider_request_id": provider_request_id,
        **report.model_dump(mode="json"),
    }


def build_verification_payload(
    details: Mapping[str, object],
) -> dict[str, object]:
    """Shape one verifier result for the canonical validation event."""

    return {
        "model_role": "VERIFIER",
        "decision_id": details["decision_id"],
        "claim_id": details["claim_id"],
        "service_line_id": details["service_line_id"],
        "outcome": details["outcome"],
        "reason_code": details["reason_code"],
        "requires_manual_review": details["requires_manual_review"],
        "evidence_run_id": details["evidence_run_id"],
        "knowledge_snapshot_id": details["knowledge_snapshot_id"],
        "bundle_fingerprint": details["bundle_fingerprint"],
        "validation_problems": details["validation_problems"],
        "elapsed_ms": details["elapsed_ms"],
    }
