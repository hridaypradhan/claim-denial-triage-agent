import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from claim_agent.contracts import (
    CriterionAssessment,
    CriterionStatus,
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
    VERIFIER_REASON_FORMULA_NOT_SATISFIED,
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


class TestVerifierContractAndValidation(unittest.TestCase):
    def test_verifier_input_enforces_no_tools_and_freezes_nested_passages(self):
        v_input = _load_input()
        self.assertEqual(v_input.run_id, v_input.evidence_bundle.run_id)
        self.assertEqual(
            v_input.knowledge_snapshot_id,
            v_input.evidence_bundle.knowledge_snapshot_id,
        )
        self.assertEqual(
            v_input.bundle_fingerprint, v_input.evidence_bundle.fingerprint()
        )

        # Nested passage inside VerifierInput.evidence_bundle must be immutable
        with self.assertRaises(ValidationError):
            v_input.evidence_bundle.passages[0].passage.text = "mutated"  # type: ignore[misc]

        raw_tools = json.loads(
            (FIXTURE_DIR / "valid_agree_input.json").read_text(encoding="utf-8")
        )
        raw_tools["authorized_tools"] = ["get_encounter_note"]
        with self.assertRaises(ValidationError):
            VerifierInput.model_validate_json(json.dumps(raw_tools))

    def test_verifier_input_rejects_mismatched_bundle_scope_and_tracks_run_snapshot_changes(self):
        raw = json.loads(
            (FIXTURE_DIR / "valid_agree_input.json").read_text(encoding="utf-8")
        )

        wrong_scope = json.loads(json.dumps(raw))
        wrong_scope["evidence_bundle"]["scope"]["claim_id"] = "CLM-2026-OTHER"
        for p in wrong_scope["evidence_bundle"]["passages"]:
            p["claim_id"] = "CLM-2026-OTHER"
        with self.assertRaises(ValidationError):
            VerifierInput.model_validate_json(json.dumps(wrong_scope))

        tampered_passage = json.loads(json.dumps(raw))
        tampered_passage["evidence_bundle"]["passages"][0]["claim_id"] = "CLM-2026-OTHER"
        with self.assertRaises(ValidationError):
            VerifierInput.model_validate_json(json.dumps(tampered_passage))

        v_orig = _load_input()
        alt_run = json.loads(json.dumps(raw))
        alt_run["evidence_bundle"]["run_id"] = "run-005097-02"
        v_alt_run = VerifierInput.model_validate_json(json.dumps(alt_run))
        self.assertNotEqual(v_alt_run.bundle_fingerprint, v_orig.bundle_fingerprint)

        alt_snap = json.loads(json.dumps(raw))
        alt_snap["evidence_bundle"]["knowledge_snapshot_id"] = "knowledge-v2"
        v_alt_snap = VerifierInput.model_validate_json(json.dumps(alt_snap))
        self.assertNotEqual(v_alt_snap.bundle_fingerprint, v_orig.bundle_fingerprint)

    def test_verifier_agree_with_valid_same_run_citations(self):
        v_input = _load_input()
        raw_out = (FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8")

        res = validate_verifier_response(
            v_input, _accepted_deterministic(v_input), raw_output=raw_out
        )

        self.assertEqual(res.outcome, VerificationOutcome.AGREE)
        self.assertFalse(res.requires_manual_review)
        self.assertEqual(res.reason_code, VERIFIER_REASON_AGREED)
        self.assertEqual(res.run_id, v_input.run_id)
        self.assertEqual(res.knowledge_snapshot_id, v_input.knowledge_snapshot_id)
        self.assertEqual(res.bundle_fingerprint, v_input.bundle_fingerprint)
        self.assertIsNotNone(res.validated_report)

    def test_verifier_result_preserves_bundle_provenance_across_runs_snapshots_and_outcomes(self):
        raw = json.loads(
            (FIXTURE_DIR / "valid_agree_input.json").read_text(encoding="utf-8")
        )
        v_orig = _load_input()
        det_orig = _accepted_deterministic(v_orig)

        alt_run_raw = json.loads(json.dumps(raw))
        alt_run_raw["evidence_bundle"]["run_id"] = "run-005097-99"
        v_alt_run = VerifierInput.model_validate_json(json.dumps(alt_run_raw))

        alt_snap_raw = json.loads(json.dumps(raw))
        alt_snap_raw["evidence_bundle"]["knowledge_snapshot_id"] = "knowledge-v9"
        v_alt_snap = VerifierInput.model_validate_json(json.dumps(alt_snap_raw))

        agree_raw = (FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8")
        res_orig = validate_verifier_response(v_orig, det_orig, raw_output=agree_raw)
        res_alt_run = validate_verifier_response(
            v_alt_run, _accepted_deterministic(v_alt_run), raw_output=agree_raw
        )
        res_alt_snap = validate_verifier_response(
            v_alt_snap, _accepted_deterministic(v_alt_snap), raw_output=agree_raw
        )

        # Results from different runs or knowledge snapshots must carry distinct provenance
        self.assertEqual(res_alt_run.run_id, "run-005097-99")
        self.assertNotEqual(res_alt_run.bundle_fingerprint, res_orig.bundle_fingerprint)
        self.assertEqual(res_alt_snap.knowledge_snapshot_id, "knowledge-v9")
        self.assertNotEqual(res_alt_snap.bundle_fingerprint, res_orig.bundle_fingerprint)

        # Non-AGREE outcomes also preserve originating bundle provenance
        disagree_raw = (FIXTURE_DIR / "disagree_output.json").read_text(encoding="utf-8")
        uncertain_raw = (FIXTURE_DIR / "uncertain_output.json").read_text(encoding="utf-8")
        for non_agree_res in (
            validate_verifier_response(v_orig, det_orig, raw_output=disagree_raw),
            validate_verifier_response(v_orig, det_orig, raw_output=uncertain_raw),
            validate_verifier_response(v_orig, det_orig, raw_output="{bad-json"),
            validate_verifier_response(v_orig, det_orig, provider_error="HTTP 503"),
            validate_verifier_response(v_orig, det_orig, timed_out=True),
        ):
            self.assertEqual(non_agree_res.run_id, v_orig.run_id)
            self.assertEqual(
                non_agree_res.knowledge_snapshot_id, v_orig.knowledge_snapshot_id
            )
            self.assertEqual(
                non_agree_res.bundle_fingerprint, v_orig.bundle_fingerprint
            )

    def test_verifier_agree_enforces_policy_formula_coverage_and_allows_valid_or_branches(self):
        base_raw = json.loads(
            (FIXTURE_DIR / "valid_agree_input.json").read_text(encoding="utf-8")
        )
        base_raw["proposed_decision"]["criterion_assessments"] = [
            {
                "criterion_id": "CRIT-A",
                "status": CriterionStatus.SUPPORTED.value,
                "explanation": "6 weeks PT documented.",
                "citation_ids": ["ENC-CLM-2026-005097-CN@v1#p1"],
            },
            {
                "criterion_id": "CRIT-B",
                "status": CriterionStatus.SUPPORTED.value,
                "explanation": "Neurologic deficit documented.",
                "citation_ids": ["ENC-CLM-2026-005097-CN@v1#p1"],
            },
            {
                "criterion_id": "CRIT-C",
                "status": CriterionStatus.SUPPORTED.value,
                "explanation": "Red flag myelopathy documented.",
                "citation_ids": ["ENC-CLM-2026-005097-CN@v1#p1"],
            },
        ]

        and_raw = json.loads(json.dumps(base_raw))
        and_raw["evidence_bundle"]["policy_formula"] = "CRIT-A AND CRIT-B"
        v_and = VerifierInput.model_validate_json(json.dumps(and_raw))

        partial_agree = json.loads(
            (FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8")
        )
        partial_agree["supported_criterion_ids"] = ["CRIT-A"]

        res_partial = validate_verifier_response(
            v_and,
            _accepted_deterministic(v_and),
            raw_output=json.dumps(partial_agree),
        )
        self.assertEqual(res_partial.outcome, VerificationOutcome.INVALID_OUTPUT)
        self.assertTrue(res_partial.requires_manual_review)
        self.assertEqual(
            res_partial.reason_code, VERIFIER_REASON_FORMULA_NOT_SATISFIED
        )

        or_raw = json.loads(json.dumps(base_raw))
        or_raw["evidence_bundle"]["policy_formula"] = "(CRIT-A AND CRIT-B) OR CRIT-C"
        v_or = VerifierInput.model_validate_json(json.dumps(or_raw))

        alt_branch_agree = json.loads(
            (FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8")
        )
        alt_branch_agree["supported_criterion_ids"] = ["CRIT-C"]

        res_branch = validate_verifier_response(
            v_or,
            _accepted_deterministic(v_or),
            raw_output=json.dumps(alt_branch_agree),
        )
        self.assertEqual(res_branch.outcome, VerificationOutcome.AGREE)
        self.assertFalse(res_branch.requires_manual_review)
        self.assertEqual(res_branch.reason_code, VERIFIER_REASON_AGREED)

    def test_verifier_rejects_mismatched_deterministic_validation_identity_or_decision(self):
        v_input = _load_input()
        valid_agree_raw = (FIXTURE_DIR / "valid_agree_output.json").read_text(
            encoding="utf-8"
        )

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
        self.assertEqual(res_mismatch.outcome, VerificationOutcome.INVALID_OUTPUT)
        self.assertTrue(res_mismatch.requires_manual_review)
        self.assertEqual(
            res_mismatch.reason_code, VERIFIER_REASON_VALIDATION_MISMATCH
        )

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
        self.assertEqual(res_altered.outcome, VerificationOutcome.INVALID_OUTPUT)
        self.assertTrue(res_altered.requires_manual_review)
        self.assertEqual(
            res_altered.reason_code, VERIFIER_REASON_VALIDATION_MISMATCH
        )

    def test_verifier_rejects_incoherent_agree_and_unknown_criterion_ids(self):
        v_input = _load_input()
        det = _accepted_deterministic(v_input)

        incoherent = json.loads(
            (FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8")
        )
        incoherent["disputed_criterion_ids"] = ["CRIT-A"]
        incoherent["unresolved_questions"] = ["Evidence is contradictory"]
        res_incoherent = validate_verifier_response(
            v_input, det, raw_output=json.dumps(incoherent)
        )
        self.assertEqual(res_incoherent.outcome, VerificationOutcome.INVALID_OUTPUT)
        self.assertTrue(res_incoherent.requires_manual_review)
        self.assertEqual(
            res_incoherent.reason_code, VERIFIER_REASON_INCOHERENT_AGREE
        )

        unknown_crit = json.loads(
            (FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8")
        )
        unknown_crit["supported_criterion_ids"] = ["CRIT-DOES-NOT-EXIST"]
        res_unknown = validate_verifier_response(
            v_input, det, raw_output=json.dumps(unknown_crit)
        )
        self.assertEqual(res_unknown.outcome, VerificationOutcome.INVALID_OUTPUT)
        self.assertTrue(res_unknown.requires_manual_review)
        self.assertEqual(res_unknown.reason_code, VERIFIER_REASON_INVALID_OUTPUT)

    def test_verifier_disagree_and_uncertain_require_manual_review(self):
        v_input = _load_input()
        disagree_raw = (FIXTURE_DIR / "disagree_output.json").read_text(
            encoding="utf-8"
        )

        res_disagree = validate_verifier_response(
            v_input, _accepted_deterministic(v_input), raw_output=disagree_raw
        )
        self.assertEqual(res_disagree.outcome, VerificationOutcome.DISAGREE)
        self.assertTrue(res_disagree.requires_manual_review)
        self.assertEqual(res_disagree.reason_code, VERIFIER_REASON_DISAGREED)

        uncertain_raw = (FIXTURE_DIR / "uncertain_output.json").read_text(
            encoding="utf-8"
        )
        res_uncertain = validate_verifier_response(
            v_input, _accepted_deterministic(v_input), raw_output=uncertain_raw
        )
        self.assertEqual(res_uncertain.outcome, VerificationOutcome.UNCERTAIN)
        self.assertTrue(res_uncertain.requires_manual_review)
        self.assertEqual(res_uncertain.reason_code, VERIFIER_REASON_UNCERTAIN)

    def test_verifier_malformed_timeout_and_provider_failure_fail_closed(self):
        v_input = _load_input()
        det = _accepted_deterministic(v_input)

        res_bad_json = validate_verifier_response(
            v_input, det, raw_output="{not-valid-json"
        )
        self.assertEqual(res_bad_json.outcome, VerificationOutcome.INVALID_OUTPUT)
        self.assertTrue(res_bad_json.requires_manual_review)
        self.assertEqual(res_bad_json.reason_code, VERIFIER_REASON_INVALID_OUTPUT)

        res_timeout = validate_verifier_response(v_input, det, timed_out=True)
        self.assertEqual(res_timeout.outcome, VerificationOutcome.TIMEOUT)
        self.assertTrue(res_timeout.requires_manual_review)
        self.assertEqual(res_timeout.reason_code, VERIFIER_REASON_TIMEOUT)

        res_err = validate_verifier_response(
            v_input, det, provider_error="beam-1-0 HTTP 503"
        )
        self.assertEqual(res_err.outcome, VerificationOutcome.PROVIDER_FAILURE)
        self.assertTrue(res_err.requires_manual_review)
        self.assertEqual(res_err.reason_code, VERIFIER_REASON_PROVIDER_FAILURE)

        for fixture_name in (
            "invalid_output_result.json",
            "provider_failure_result.json",
            "timeout_result.json",
        ):
            parsed = VerifierValidationResult.model_validate_json(
                (FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")
            )
            self.assertTrue(parsed.requires_manual_review)
            self.assertEqual(parsed.run_id, v_input.run_id)
            self.assertEqual(
                parsed.knowledge_snapshot_id, v_input.knowledge_snapshot_id
            )
            self.assertEqual(parsed.bundle_fingerprint, v_input.bundle_fingerprint)

    def test_verifier_rejects_unretrieved_or_duplicate_citation_and_cannot_override_rejection(self):
        v_input = _load_input()
        det_accepted = _accepted_deterministic(v_input)

        bad_cite = json.loads(
            (FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8")
        )
        bad_cite["citation_ids"] = ["ENC-OTHER-CLAIM@v1#p1"]
        res_cite = validate_verifier_response(
            v_input, det_accepted, raw_output=json.dumps(bad_cite)
        )
        self.assertEqual(res_cite.outcome, VerificationOutcome.INVALID_OUTPUT)
        self.assertTrue(res_cite.requires_manual_review)

        dup_cite = json.loads(
            (FIXTURE_DIR / "valid_agree_output.json").read_text(encoding="utf-8")
        )
        dup_cite["citation_ids"] = ["POL-UHC-72148@v1#p1", "POL-UHC-72148@v1#p1"]
        res_dup = validate_verifier_response(
            v_input, det_accepted, raw_output=json.dumps(dup_cite)
        )
        self.assertEqual(res_dup.outcome, VerificationOutcome.INVALID_OUTPUT)
        self.assertTrue(res_dup.requires_manual_review)

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
        valid_agree_raw = (FIXTURE_DIR / "valid_agree_output.json").read_text(
            encoding="utf-8"
        )
        res_override = validate_verifier_response(
            v_input, det_rejected, raw_output=valid_agree_raw
        )
        self.assertTrue(res_override.requires_manual_review)
        self.assertEqual(
            res_override.reason_code, VERIFIER_REASON_DETERMINISTIC_REJECTED
        )


if __name__ == "__main__":
    unittest.main()