import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from claim_agent.contracts import (
    AppealDraft,
    Disposition,
    DraftingInput,
    DraftingOutcome,
    ValidationOutcome,
    ValidationProblem,
    VerificationOutcome,
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
    DRAFTING_REASON_VERIFIER_MISMATCH,
    check_drafting_authorization,
    render_grounded_appeal_body,
    validate_appeal_draft,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "contracts" / "drafting"


def _load_input() -> DraftingInput:
    raw = (FIXTURE_DIR / "valid_draft_input.json").read_text(encoding="utf-8")
    return DraftingInput.model_validate_json(raw)


class TestDraftingAuthorizationAndGroundedness(unittest.TestCase):
    def test_drafting_input_delegates_bundle_provenance_and_freezes_passages(self):
        d_input = _load_input()
        self.assertEqual(d_input.run_id, d_input.evidence_bundle.run_id)
        self.assertEqual(
            d_input.knowledge_snapshot_id,
            d_input.evidence_bundle.knowledge_snapshot_id,
        )
        self.assertEqual(
            d_input.bundle_fingerprint, d_input.evidence_bundle.fingerprint()
        )

        # Nested passage inside DraftingInput.evidence_bundle must be immutable
        with self.assertRaises(ValidationError):
            d_input.evidence_bundle.passages[0].passage.text = "mutated"  # type: ignore[misc]

        raw = json.loads(
            (FIXTURE_DIR / "valid_draft_input.json").read_text(encoding="utf-8")
        )
        raw["evidence_bundle"]["scope"]["claim_id"] = "CLM-2026-OTHER"
        for p in raw["evidence_bundle"]["passages"]:
            p["claim_id"] = "CLM-2026-OTHER"
        with self.assertRaisesRegex(ValidationError, "evidence_bundle.scope"):
            DraftingInput.model_validate_json(json.dumps(raw))

    def test_forged_or_mismatched_verifier_result_cannot_authorize_drafting(self):
        d_input = _load_input()
        self.assertIsNone(check_drafting_authorization(d_input))

        # 1. Forged boolean verification_agreed=True is rejected by strict schema
        raw_bool = json.loads(
            (FIXTURE_DIR / "valid_draft_input.json").read_text(encoding="utf-8")
        )
        raw_bool["verification_agreed"] = True
        with self.assertRaises(ValidationError):
            DraftingInput.model_validate_json(json.dumps(raw_bool))

        # 2. Non-AGREE verifier outcome (DISAGREE) -> unauthorized
        disagreed_ver = d_input.verifier_result.model_copy(
            update={
                "outcome": VerificationOutcome.DISAGREE,
                "requires_manual_review": True,
                "reason_code": "VERIFIER_DISAGREED",
            }
        )
        res_disagree = validate_appeal_draft(
            d_input.model_copy(update={"verifier_result": disagreed_ver})
        )
        self.assertEqual(res_disagree.outcome, DraftingOutcome.REJECTED_UNAUTHORIZED)
        self.assertTrue(res_disagree.requires_manual_review)
        self.assertEqual(res_disagree.reason_code, DRAFTING_REASON_NOT_VERIFIED)

        # 3. Verifier result from a different run / bundle fingerprint -> rejected
        wrong_run_ver = d_input.verifier_result.model_copy(
            update={
                "run_id": "run-005097-99",
                "bundle_fingerprint": "0" * 64,
            }
        )
        res_wrong_bundle = validate_appeal_draft(
            d_input.model_copy(update={"verifier_result": wrong_run_ver})
        )
        self.assertEqual(
            res_wrong_bundle.outcome, DraftingOutcome.REJECTED_UNAUTHORIZED
        )
        self.assertEqual(
            res_wrong_bundle.reason_code, DRAFTING_REASON_VERIFIER_MISMATCH
        )

        # 4. Verifier result for a different decision_id -> rejected
        wrong_dec_ver = d_input.verifier_result.model_copy(
            update={"decision_id": "dec-other-999"}
        )
        res_wrong_dec = validate_appeal_draft(
            d_input.model_copy(update={"verifier_result": wrong_dec_ver})
        )
        self.assertEqual(res_wrong_dec.outcome, DraftingOutcome.REJECTED_UNAUTHORIZED)
        self.assertEqual(res_wrong_dec.reason_code, DRAFTING_REASON_VERIFIER_MISMATCH)

    def test_drafting_impossible_before_deterministic_validation_succeeds(self):
        d_input = _load_input()

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
        res_not_val = validate_appeal_draft(
            d_input.model_copy(update={"validation_result": rejected_val})
        )
        self.assertEqual(res_not_val.outcome, DraftingOutcome.REJECTED_UNAUTHORIZED)
        self.assertEqual(res_not_val.reason_code, DRAFTING_REASON_NOT_VALIDATED)

        bad_disp_dec = d_input.validation_result.validated_decision.model_copy(
            update={"recommended_disposition": Disposition.BILL_PATIENT}
        )
        bad_disp_val = d_input.validation_result.model_copy(
            update={"validated_decision": bad_disp_dec}
        )
        res_disp = validate_appeal_draft(
            d_input.model_copy(update={"validation_result": bad_disp_val})
        )
        self.assertEqual(res_disp.outcome, DraftingOutcome.REJECTED_UNAUTHORIZED)
        self.assertEqual(res_disp.reason_code, DRAFTING_REASON_DISALLOWED_DISPOSITION)

    def test_accepted_draft_always_remains_pending_review_and_allows_case_whitespace_quote(self):
        d_input = _load_input()
        raw_draft = (FIXTURE_DIR / "valid_draft_output.json").read_text(
            encoding="utf-8"
        )

        res = validate_appeal_draft(d_input, raw_output=raw_draft)

        self.assertEqual(res.outcome, DraftingOutcome.ACCEPTED)
        self.assertFalse(res.requires_manual_review)
        self.assertEqual(res.reason_code, DRAFTING_REASON_ACCEPTED)
        self.assertEqual(res.approval_state, "PENDING_REVIEW")
        self.assertIsNotNone(res.validated_draft)
        self.assertEqual(res.validated_draft.approval_state, "PENDING_REVIEW")
        self.assertEqual(
            res.validated_draft.body,
            render_grounded_appeal_body(res.validated_draft.grounded_claims),
        )

        # Case and whitespace normalization on cited passage quotes is accepted
        normalized_payload = json.loads(raw_draft)
        normalized_payload["grounded_claims"][1]["statement"] = (
            "  patient   completed 6 weeks of physical therapy without relief.  "
        )
        parsed_norm = AppealDraft.model_validate_json(
            json.dumps(
                {
                    **normalized_payload,
                    "body": "placeholder",
                }
            )
        )
        normalized_payload["body"] = render_grounded_appeal_body(
            parsed_norm.grounded_claims
        )
        res_norm = validate_appeal_draft(
            d_input, raw_output=json.dumps(normalized_payload)
        )
        self.assertEqual(res_norm.outcome, DraftingOutcome.ACCEPTED)

        for forbidden_state in ("APPROVED", "SUBMITTED"):
            bad = json.loads(raw_draft)
            bad["approval_state"] = forbidden_state
            with self.assertRaises(ValidationError):
                AppealDraft.model_validate_json(json.dumps(bad))

    def test_negated_or_reversed_claims_and_body_are_rejected(self):
        d_input = _load_input()
        base_payload = json.loads(
            (FIXTURE_DIR / "valid_draft_output.json").read_text(encoding="utf-8")
        )

        # 1. Claim reversing evidence with "never":
        # Passage: "Patient completed 6 weeks of physical therapy without relief."
        never_claim = json.loads(json.dumps(base_payload))
        never_claim["grounded_claims"][1]["statement"] = (
            "Patient never completed 6 weeks of physical therapy."
        )
        parsed_never = AppealDraft.model_validate_json(json.dumps(never_claim))
        never_claim["body"] = render_grounded_appeal_body(parsed_never.grounded_claims)
        res_never = validate_appeal_draft(
            d_input, raw_output=json.dumps(never_claim)
        )
        self.assertEqual(res_never.outcome, DraftingOutcome.REJECTED_UNGROUNDED)
        self.assertTrue(res_never.requires_manual_review)
        self.assertEqual(res_never.reason_code, DRAFTING_REASON_UNGROUNDED_CLAIM)

        # 2. Claim reversing "without relief" to "with relief":
        with_relief_claim = json.loads(json.dumps(base_payload))
        with_relief_claim["grounded_claims"][1]["statement"] = (
            "Patient completed 6 weeks of physical therapy with relief."
        )
        parsed_relief = AppealDraft.model_validate_json(
            json.dumps(with_relief_claim)
        )
        with_relief_claim["body"] = render_grounded_appeal_body(
            parsed_relief.grounded_claims
        )
        res_relief = validate_appeal_draft(
            d_input, raw_output=json.dumps(with_relief_claim)
        )
        self.assertEqual(res_relief.outcome, DraftingOutcome.REJECTED_UNGROUNDED)
        self.assertTrue(res_relief.requires_manual_review)
        self.assertEqual(res_relief.reason_code, DRAFTING_REASON_UNGROUNDED_CLAIM)

        # 3. Body reversing policy requirement ("not required") despite valid claims:
        reversed_body = json.loads(json.dumps(base_payload))
        reversed_body["body"] = (
            "Conservative therapy of at least 6 weeks is not required. "
            "[POL-UHC-72148@v1#p1] Patient completed 6 weeks of physical therapy "
            "without relief. [ENC-CLM-2026-005097-CN@v1#p1]"
        )
        res_rev_body = validate_appeal_draft(
            d_input, raw_output=json.dumps(reversed_body)
        )
        self.assertEqual(res_rev_body.outcome, DraftingOutcome.REJECTED_UNGROUNDED)
        self.assertTrue(res_rev_body.requires_manual_review)
        self.assertEqual(res_rev_body.reason_code, DRAFTING_REASON_UNGROUNDED_CLAIM)

    def test_permitted_citation_attached_to_unsupported_claim_is_rejected(self):
        d_input = _load_input()
        draft_payload = json.loads(
            (FIXTURE_DIR / "valid_draft_output.json").read_text(encoding="utf-8")
        )
        draft_payload["grounded_claims"] = [
            {
                "statement": "Emergency inpatient surgery was pre-authorized by the medical director.",
                "citation_ids": ["POL-UHC-72148@v1#p1"],
            }
        ]
        draft_payload["citation_ids"] = ["POL-UHC-72148@v1#p1"]
        parsed = AppealDraft.model_validate_json(json.dumps(draft_payload))
        draft_payload["body"] = render_grounded_appeal_body(parsed.grounded_claims)

        res = validate_appeal_draft(d_input, raw_output=json.dumps(draft_payload))

        self.assertEqual(res.outcome, DraftingOutcome.REJECTED_UNGROUNDED)
        self.assertTrue(res.requires_manual_review)
        self.assertEqual(res.reason_code, DRAFTING_REASON_UNGROUNDED_CLAIM)
        self.assertIsNone(res.validated_draft)

    def test_factual_body_assertion_omitted_from_grounded_claims_is_rejected(self):
        d_input = _load_input()
        draft_payload = json.loads(
            (FIXTURE_DIR / "valid_draft_output.json").read_text(encoding="utf-8")
        )
        draft_payload["body"] += " The payer already approved this appeal."

        res = validate_appeal_draft(d_input, raw_output=json.dumps(draft_payload))

        self.assertEqual(res.outcome, DraftingOutcome.REJECTED_UNGROUNDED)
        self.assertTrue(res.requires_manual_review)
        self.assertEqual(res.reason_code, DRAFTING_REASON_UNGROUNDED_CLAIM)
        self.assertIsNone(res.validated_draft)

    def test_unresolved_citations_reject_draft(self):
        d_input = _load_input()
        draft_payload = json.loads(
            (FIXTURE_DIR / "valid_draft_output.json").read_text(encoding="utf-8")
        )
        draft_payload["citation_ids"].append("ENC-UNRETRIEVED-999@v1#p1")
        draft_payload["grounded_claims"].append(
            {
                "statement": "Patient completed 6 weeks of physical therapy without relief.",
                "citation_ids": ["ENC-UNRETRIEVED-999@v1#p1"],
            }
        )
        parsed = AppealDraft.model_validate_json(json.dumps(draft_payload))
        draft_payload["body"] = render_grounded_appeal_body(parsed.grounded_claims)

        res = validate_appeal_draft(d_input, raw_output=json.dumps(draft_payload))

        self.assertEqual(res.outcome, DraftingOutcome.REJECTED_UNGROUNDED)
        self.assertTrue(res.requires_manual_review)
        self.assertEqual(res.reason_code, DRAFTING_REASON_UNGROUNDED_CLAIM)
        self.assertIsNone(res.validated_draft)

    def test_drafting_malformed_timeout_and_provider_failure_fail_closed(self):
        d_input = _load_input()

        res_bad = validate_appeal_draft(d_input, raw_output="{bad-json")
        self.assertEqual(res_bad.outcome, DraftingOutcome.INVALID_OUTPUT)
        self.assertTrue(res_bad.requires_manual_review)
        self.assertEqual(res_bad.reason_code, DRAFTING_REASON_INVALID_OUTPUT)

        res_timeout = validate_appeal_draft(d_input, timed_out=True)
        self.assertEqual(res_timeout.outcome, DraftingOutcome.TIMEOUT)
        self.assertTrue(res_timeout.requires_manual_review)
        self.assertEqual(res_timeout.reason_code, DRAFTING_REASON_TIMEOUT)

        res_err = validate_appeal_draft(
            d_input, provider_error="beam-1-0 connection reset"
        )
        self.assertEqual(res_err.outcome, DraftingOutcome.PROVIDER_FAILURE)
        self.assertTrue(res_err.requires_manual_review)
        self.assertEqual(res_err.reason_code, DRAFTING_REASON_PROVIDER_FAILURE)


if __name__ == "__main__":
    unittest.main()