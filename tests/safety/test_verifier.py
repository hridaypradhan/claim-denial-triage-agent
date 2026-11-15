import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from claim_agent.contracts import (
    ValidationOutcome,
    ValidationProblem,
    ValidationResult,
    VerificationOutcome,
    VerifierInput,
    VerifierValidationResult,
)
from claim_agent.safety.verifier import (
    VERIFIER_REASON_AGREED,
    VERIFIER_REASON_DETERMINISTIC_REJECTED,
    VERIFIER_REASON_DISAGREED,
    VERIFIER_REASON_INCOHERENT_AGREE,
    VERIFIER_REASON_INVALID_OUTPUT,
    VERIFIER_REASON_PROVIDER_FAILURE,
    VERIFIER_REASON_TIMEOUT,
    VERIFIER_REASON_UNCERTAIN,
    VERIFIER_REASON_VALIDATION_MISMATCH,
    validate_verifier_response,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "contracts" / "verifier"


def _load_input() -> VerifierInput:
    raw = (FIXTURE_DIR / "valid_agree_input.json").read_text(encoding="utf-8")
    return VerifierInput.model_validate_json(raw)


def _accepted_deterministic(v_input: VerifierInput) -> ValidationResult:
    decision = v_input.proposed_decision
    return ValidationResult(
        decision_id=decision.decision_id,
        claim_id=decision.claim_id,
        service_line_id=decision.service_line_id,
        outcome=ValidationOutcome.ACCEPTED,
        validated_decision=decision,
    )


def test_verifier_input_enforces_no_tools_and_delegates_bundle_provenance():
    v_input = _load_input()
    assert v_input.run_id == v_input.evidence_bundle.run_id
    assert v_input.knowledge_snapshot_id == v_input.evidence_bundle.knowledge_snapshot_id
    assert v_input.bundle_fingerprint == v_input.evidence_bundle.fingerprint()

    raw_tools = json.loads((FIXTURE_DIR / "valid_agree_input.json").read_text(encoding="utf-8"))
    raw_tools["authorized_tools"] = ["get_encounter_note"]
    with pytest.raises(ValidationError, match="no tool authority"):
        VerifierInput.model_validate_json(json.dumps(raw_tools))


def test_verifier_input_rejects_mismatched_bundle_scope_and_tracks_run_snapshot_changes():
    raw = json.loads((FIXTURE_DIR / "valid_agree_input.json").read_text(encoding="utf-8"))

    # 1. Mismatched claim scope between proposed_decision and evidence_bundle
    wrong_scope = json.loads(json.dumps(raw))
    wrong_scope["evidence_bundle"]["scope"]["claim_id"] = "CLM-2026-OTHER"
    for p in wrong_scope["evidence_bundle"]["passages"]:
        p["claim_id"] = "CLM-2026-OTHER"
    with pytest.raises(ValidationError, match="evidence_bundle.scope"):
        VerifierInput.model_validate_json(json.dumps(wrong_scope))

    # 2. Tampered passage provenance inside EvidenceBundle
    tampered_passage = json.loads(json.dumps(raw))
    tampered_passage["evidence_bundle"]["passages"][0]["claim_id"] = "CLM-2026-OTHER"
    with pytest.raises(ValidationError, match="different claim scope"):
        VerifierInput.model_validate_json(json.dumps(tampered_passage))

    # 3. Changing run_id or knowledge_snapshot_id changes the canonical bundle fingerprint
    v_orig = _load_input()
    alt_run = json.loads(json.dumps(raw))
    alt_run["evidence_bundle"]["run_id"] = "run-005097-02"
    v_alt_run = VerifierInput.model_validate_json(json.dumps(alt_run))
    assert v_alt_run.bundle_fingerprint != v_orig.bundle_fingerprint

    alt_snap = json.loads(json.dumps(raw))
    alt_snap["evidence_bundle"]["knowledge_snapshot_id"] = "knowledge-v2"
    v_alt_snap = VerifierInput.model_validate_json(json.dumps(alt_snap))
    assert v_alt_snap.bundle_fingerprint != v_orig.bundle_fingerprint


def test_verifier_agree_with_valid_same_run_citations():
    v_input = _load_input()
    raw_out = (FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8")

    res = validate_verifier_response(
        v_input, _accepted_deterministic(v_input), raw_output=raw_out
    )

    assert res.outcome == VerificationOutcome.AGREE
    assert res.requires_manual_review is False
    assert res.reason_code == VERIFIER_REASON_AGREED
    assert res.validated_report is not None


def test_verifier_rejects_mismatched_deterministic_validation_identity_or_decision():
    v_input = _load_input()
    valid_agree_raw = (FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8")

    other_decision = v_input.proposed_decision.model_copy(
        update={
            "decision_id": "dec-other-999",
            "claim_id": "CLM-2026-999999",
        }
    )
    mismatched_validation = ValidationResult(
        decision_id="dec-other-999",
        claim_id="CLM-2026-999999",
        service_line_id=1,
        outcome=ValidationOutcome.ACCEPTED,
        validated_decision=other_decision,
    )

    res_mismatch = validate_verifier_response(
        v_input, mismatched_validation, raw_output=valid_agree_raw
    )
    assert res_mismatch.outcome == VerificationOutcome.INVALID_OUTPUT
    assert res_mismatch.requires_manual_review is True
    assert res_mismatch.reason_code == VERIFIER_REASON_VALIDATION_MISMATCH

    altered_decision = v_input.proposed_decision.model_copy(
        update={"rationale": "Tampered rationale."}
    )
    altered_validation = ValidationResult(
        decision_id=v_input.proposed_decision.decision_id,
        claim_id=v_input.proposed_decision.claim_id,
        service_line_id=v_input.proposed_decision.service_line_id,
        outcome=ValidationOutcome.ACCEPTED,
        validated_decision=altered_decision,
    )
    res_altered = validate_verifier_response(
        v_input, altered_validation, raw_output=valid_agree_raw
    )
    assert res_altered.outcome == VerificationOutcome.INVALID_OUTPUT
    assert res_altered.requires_manual_review is True
    assert res_altered.reason_code == VERIFIER_REASON_VALIDATION_MISMATCH


def test_verifier_rejects_incoherent_agree_and_unknown_criterion_ids():
    v_input = _load_input()
    det = _accepted_deterministic(v_input)

    incoherent = json.loads((FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8"))
    incoherent["disputed_criterion_ids"] = ["CRIT-A"]
    incoherent["unresolved_questions"] = ["Evidence is contradictory"]
    res_incoherent = validate_verifier_response(
        v_input, det, raw_output=json.dumps(incoherent)
    )
    assert res_incoherent.outcome == VerificationOutcome.INVALID_OUTPUT
    assert res_incoherent.requires_manual_review is True
    assert res_incoherent.reason_code == VERIFIER_REASON_INCOHERENT_AGREE

    unknown_crit = json.loads((FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8"))
    unknown_crit["supported_criterion_ids"] = ["CRIT-DOES-NOT-EXIST"]
    res_unknown = validate_verifier_response(
        v_input, det, raw_output=json.dumps(unknown_crit)
    )
    assert res_unknown.outcome == VerificationOutcome.INVALID_OUTPUT
    assert res_unknown.requires_manual_review is True
    assert res_unknown.reason_code == VERIFIER_REASON_INVALID_OUTPUT


def test_verifier_disagree_and_uncertain_require_manual_review():
    v_input = _load_input()
    disagree_raw = (FIXTURE_DIR / "disagree_output.json").read_text(encoding="utf-8")

    res_disagree = validate_verifier_response(
        v_input, _accepted_deterministic(v_input), raw_output=disagree_raw
    )
    assert res_disagree.outcome == VerificationOutcome.DISAGREE
    assert res_disagree.requires_manual_review is True
    assert res_disagree.reason_code == VERIFIER_REASON_DISAGREED

    uncertain_raw = (FIXTURE_DIR / "uncertain_output.json").read_text(encoding="utf-8")
    res_uncertain = validate_verifier_response(
        v_input, _accepted_deterministic(v_input), raw_output=uncertain_raw
    )
    assert res_uncertain.outcome == VerificationOutcome.UNCERTAIN
    assert res_uncertain.requires_manual_review is True
    assert res_uncertain.reason_code == VERIFIER_REASON_UNCERTAIN


def test_verifier_malformed_timeout_and_provider_failure_fail_closed():
    v_input = _load_input()
    det = _accepted_deterministic(v_input)

    res_bad_json = validate_verifier_response(v_input, det, raw_output="{not-valid-json")
    assert res_bad_json.outcome == VerificationOutcome.INVALID_OUTPUT
    assert res_bad_json.requires_manual_review is True
    assert res_bad_json.reason_code == VERIFIER_REASON_INVALID_OUTPUT

    res_timeout = validate_verifier_response(v_input, det, timed_out=True)
    assert res_timeout.outcome == VerificationOutcome.TIMEOUT
    assert res_timeout.requires_manual_review is True
    assert res_timeout.reason_code == VERIFIER_REASON_TIMEOUT

    res_err = validate_verifier_response(
        v_input, det, provider_error="beam-1-0 HTTP 503"
    )
    assert res_err.outcome == VerificationOutcome.PROVIDER_FAILURE
    assert res_err.requires_manual_review is True
    assert res_err.reason_code == VERIFIER_REASON_PROVIDER_FAILURE

    for fixture_name in (
        "invalid_output_result.json",
        "provider_failure_result.json",
        "timeout_result.json",
    ):
        parsed = VerifierValidationResult.model_validate_json(
            (FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")
        )
        assert parsed.requires_manual_review is True


def test_verifier_rejects_unretrieved_or_duplicate_citation_and_cannot_override_rejection():
    v_input = _load_input()
    det_accepted = _accepted_deterministic(v_input)

    bad_cite = json.loads((FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8"))
    bad_cite["citation_ids"] = ["ENC-OTHER-CLAIM@v1#p1"]
    res_cite = validate_verifier_response(
        v_input, det_accepted, raw_output=json.dumps(bad_cite)
    )
    assert res_cite.outcome == VerificationOutcome.INVALID_OUTPUT
    assert res_cite.requires_manual_review is True

    dup_cite = json.loads((FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8"))
    dup_cite["citation_ids"] = ["POL-UHC-72148@v1#p1", "POL-UHC-72148@v1#p1"]
    res_dup = validate_verifier_response(
        v_input, det_accepted, raw_output=json.dumps(dup_cite)
    )
    assert res_dup.outcome == VerificationOutcome.INVALID_OUTPUT
    assert res_dup.requires_manual_review is True

    det_rejected = ValidationResult(
        decision_id=v_input.proposed_decision.decision_id,
        claim_id=v_input.proposed_decision.claim_id,
        service_line_id=v_input.proposed_decision.service_line_id,
        outcome=ValidationOutcome.REJECTED_MANUAL_REVIEW,
        validation_problems=[
            ValidationProblem(
                code="PROHIBITED_PATIENT_BILLING",
                field_path="recommended_disposition",
                message="Rejected deterministically.",
                is_repairable=False,
            )
        ],
        manual_review_reasons=["SAFETY_ESCALATION_PROHIBITED_BILLING"],
    )
    valid_agree_raw = (FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8")
    res_override = validate_verifier_response(
        v_input, det_rejected, raw_output=valid_agree_raw
    )
    assert res_override.requires_manual_review is True
    assert res_override.reason_code == VERIFIER_REASON_DETERMINISTIC_REJECTED