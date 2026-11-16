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

    def test_drafting_impossible_before_validation_and_verification_succeed(self):
        d_input = _load_input()
        self.assertIsNone(check_drafting_authorization(d_input))

        # 1. Verification failed/disagreed -> unauthorized
        unverified = d_input.model_copy(update={"verification_agreed": False})
        res_unverified = validate_appeal_draft(unverified)
        self.assertEqual(res_unverified.outcome, DraftingOutcome.REJECTED_UNAUTHORIZED)
        self.assertTrue(res_unverified.requires_manual_review)
        self.assertEqual(res_unverified.reason_code, DRAFTING_REASON_NOT_VERIFIED)

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
        self.assertEqual(res_not_val.outcome, DraftingOutcome.REJECTED_UNAUTHORIZED)
        self.assertEqual(res_not_val.reason_code, DRAFTING_REASON_NOT_VALIDATED)

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
        self.assertEqual(res_disp.outcome, DraftingOutcome.REJECTED_UNAUTHORIZED)
        self.assertEqual(res_disp.reason_code, DRAFTING_REASON_DISALLOWED_DISPOSITION)

    def test_accepted_draft_always_remains_pending_review(self):
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

        # Attempting to mark a draft APPROVED or SUBMITTED fails schema validation
        for forbidden_state in ("APPROVED", "SUBMITTED"):
            bad = json.loads(raw_draft)
            bad["approval_state"] = forbidden_state
            with self.assertRaises(ValidationError):
                AppealDraft.model_validate_json(json.dumps(bad))

    def test_unsupported_evidence_or_unresolved_citations_reject_draft(self):
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