import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from claim_agent.contracts import (
    AppealDraft,
    Disposition,
    DraftingInput,
    DraftingOutcome,
    ValidationOutcome,
    ValidationProblem,
)
from claim_agent.safety.drafting import (
    DRAFTING_REASON_ACCEPTED,
    DRAFTING_REASON_DISALLOWED_DISPOSITION,
    DRAFTING_REASON_INVALID_OUTPUT,
    DRAFTING_REASON_NOT_VALIDATED,
    DRAFTING_REASON_NOT_VERIFIED,
    DRAFTING_REASON_PROVIDER_FAILURE,
    DRAFTING_REASON_TIMEOUT,
    DRAFTING_REASON_UNGROUNDED_CLAIM,
    check_drafting_authorization,
    validate_appeal_draft,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "contracts" / "drafting"


def _load_input() -> DraftingInput:
    raw = (FIXTURE_DIR / "valid_draft_input.json").read_text(encoding="utf-8")
    return DraftingInput.model_validate_json(raw)


def test_drafting_input_delegates_bundle_provenance_and_rejects_scope_mismatch():
    d_input = _load_input()
    assert d_input.run_id == d_input.evidence_bundle.run_id
    assert d_input.knowledge_snapshot_id == d_input.evidence_bundle.knowledge_snapshot_id
    assert d_input.bundle_fingerprint == d_input.evidence_bundle.fingerprint()

    raw = json.loads((FIXTURE_DIR / "valid_draft_input.json").read_text(encoding="utf-8"))
    raw["evidence_bundle"]["scope"]["claim_id"] = "CLM-2026-OTHER"
    for p in raw["evidence_bundle"]["passages"]:
        p["claim_id"] = "CLM-2026-OTHER"
    with pytest.raises(ValidationError, match="evidence_bundle.scope"):
        DraftingInput.model_validate_json(json.dumps(raw))


def test_drafting_impossible_before_validation_and_verification_succeed():
    d_input = _load_input()
    assert check_drafting_authorization(d_input) is None

    # 1. Verification failed/disagreed -> unauthorized
    unverified = d_input.model_copy(update={"verification_agreed": False})
    res_unverified = validate_appeal_draft(unverified)
    assert res_unverified.outcome == DraftingOutcome.REJECTED_UNAUTHORIZED
    assert res_unverified.requires_manual_review is True
    assert res_unverified.reason_code == DRAFTING_REASON_NOT_VERIFIED

    # 2. Deterministic validation not ACCEPTED -> unauthorized
    rejected_val = d_input.validation_result.model_copy(
        update={
            "outcome": ValidationOutcome.REJECTED_MANUAL_REVIEW,
            "validated_decision": None,
            "validation_problems": [
                ValidationProblem(
                    code="DEADLINE_EXPIRED",
                    field_path="recommended_disposition",
                    message="Expired.",
                    is_repairable=False,
                )
            ],
            "manual_review_reasons": ["SAFETY_ESCALATION_DEADLINE_EXPIRED"],
        }
    )
    not_validated = d_input.model_copy(update={"validation_result": rejected_val})
    res_not_val = validate_appeal_draft(not_validated)
    assert res_not_val.outcome == DraftingOutcome.REJECTED_UNAUTHORIZED
    assert res_not_val.reason_code == DRAFTING_REASON_NOT_VALIDATED

    # 3. Disallowed disposition (e.g., BILL_PATIENT) -> unauthorized
    bad_disp_dec = d_input.validation_result.validated_decision.model_copy(
        update={"recommended_disposition": Disposition.BILL_PATIENT}
    )
    bad_disp_val = d_input.validation_result.model_copy(
        update={"validated_decision": bad_disp_dec}
    )
    res_disp = validate_appeal_draft(
        d_input.model_copy(update={"validation_result": bad_disp_val})
    )
    assert res_disp.outcome == DraftingOutcome.REJECTED_UNAUTHORIZED
    assert res_disp.reason_code == DRAFTING_REASON_DISALLOWED_DISPOSITION


def test_accepted_draft_always_remains_pending_review():
    d_input = _load_input()
    raw_draft = (FIXTURE_DIR / "valid_draft_output.json").read_text(encoding="utf-8")

    res = validate_appeal_draft(d_input, raw_output=raw_draft)

    assert res.outcome == DraftingOutcome.ACCEPTED
    assert res.requires_manual_review is False
    assert res.reason_code == DRAFTING_REASON_ACCEPTED
    assert res.approval_state == "PENDING_REVIEW"
    assert res.validated_draft is not None
    assert res.validated_draft.approval_state == "PENDING_REVIEW"

    # Attempting to mark a draft APPROVED or SUBMITTED fails schema validation
    for forbidden_state in ("APPROVED", "SUBMITTED"):
        bad = json.loads(raw_draft)
        bad["approval_state"] = forbidden_state
        with pytest.raises(ValidationError):
            AppealDraft.model_validate_json(json.dumps(bad))


def test_unsupported_evidence_or_unresolved_citations_reject_draft():
    d_input = _load_input()
    draft_payload = json.loads(
        (FIXTURE_DIR / "valid_draft_output.json").read_text(encoding="utf-8")
    )
    draft_payload["citation_ids"].append("ENC-UNRETRIEVED-999@v1#p1")
    draft_payload["grounded_claims"].append(
        {
            "statement": "Unsupported clinical claim.",
            "citation_ids": ["ENC-UNRETRIEVED-999@v1#p1"],
        }
    )

    res = validate_appeal_draft(d_input, raw_output=json.dumps(draft_payload))

    assert res.outcome == DraftingOutcome.REJECTED_UNGROUNDED
    assert res.requires_manual_review is True
    assert res.reason_code == DRAFTING_REASON_UNGROUNDED_CLAIM
    assert res.validated_draft is None


def test_drafting_malformed_timeout_and_provider_failure_fail_closed():
    d_input = _load_input()

    res_bad = validate_appeal_draft(d_input, raw_output="{bad-json")
    assert res_bad.outcome == DraftingOutcome.INVALID_OUTPUT
    assert res_bad.requires_manual_review is True
    assert res_bad.reason_code == DRAFTING_REASON_INVALID_OUTPUT

    res_timeout = validate_appeal_draft(d_input, timed_out=True)
    assert res_timeout.outcome == DraftingOutcome.TIMEOUT
    assert res_timeout.requires_manual_review is True
    assert res_timeout.reason_code == DRAFTING_REASON_TIMEOUT

    res_err = validate_appeal_draft(
        d_input, provider_error="beam-1-0 connection reset"
    )
    assert res_err.outcome == DraftingOutcome.PROVIDER_FAILURE
    assert res_err.requires_manual_review is True
    assert res_err.reason_code == DRAFTING_REASON_PROVIDER_FAILURE