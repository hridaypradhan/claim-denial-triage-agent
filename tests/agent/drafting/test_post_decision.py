"""End-to-end tests for post-decision verification and drafting."""

from __future__ import annotations

import unittest

from claim_agent.agent.drafting.post_decision import (
    DRAFTING_ATTEMPTED,
    DETERMINISTIC_VALIDATION_REJECTED,
    VERIFICATION_NOT_AGREED,
    run_post_decision_drafting,
)
from claim_agent.agent.runtime_trace import InMemoryRuntimeTrace
from claim_agent.contracts import (
    DraftingOutcome,
    ValidationOutcome,
    ValidationResult,
)
from tests.agent.drafting.support import (
    FakeDraftingClient,
    StepClock,
    load_draft_output,
)
from tests.agent.verification.support import (
    FakeVerifierClient,
    accepted_validation,
    load_output,
    load_verifier_input,
)


class PostDecisionDraftingTests(unittest.TestCase):
    """Verify gate ordering across deterministic, verifier, and draft stages."""

    def test_validated_synthetic_case_produces_grounded_pending_draft(self) -> None:
        verifier_input = load_verifier_input()
        verifier = FakeVerifierClient(
            response_text=load_output("valid_agree_output.json")
        )
        drafter = FakeDraftingClient(response_text=load_draft_output())
        trace = InMemoryRuntimeTrace(run_id=verifier_input.run_id)

        result = run_post_decision_drafting(
            verifier_client=verifier,
            drafting_client=drafter,
            verification_request_id="run-005097-01:verify:1",
            drafting_request_id="run-005097-01:draft:1",
            proposed_decision=verifier_input.proposed_decision,
            evidence_bundle=verifier_input.evidence_bundle,
            deterministic_validation=accepted_validation(verifier_input),
            runtime_trace=trace,
            monotonic_clock=StepClock(1.0, 1.050, 2.0, 2.100),
        )

        self.assertEqual(DRAFTING_ATTEMPTED, result.stop_reason)
        self.assertIsNotNone(result.verification_execution)
        self.assertIsNotNone(result.drafting_execution)
        self.assertEqual(
            DraftingOutcome.ACCEPTED,
            result.drafting_execution.validation_result.outcome,
        )
        draft = result.validated_draft
        self.assertIsNotNone(draft)
        self.assertEqual("PENDING_REVIEW", draft.approval_state)
        self.assertEqual(1, len(verifier.received_requests))
        self.assertEqual(1, len(drafter.received_requests))

    def test_rejected_deterministic_validation_calls_neither_model(self) -> None:
        verifier_input = load_verifier_input()
        rejected = ValidationResult(
            decision_id=verifier_input.proposed_decision.decision_id,
            claim_id=verifier_input.proposed_decision.claim_id,
            service_line_id=verifier_input.proposed_decision.service_line_id,
            outcome=ValidationOutcome.REJECTED_MANUAL_REVIEW,
            manual_review_reasons=["Deterministic safety rejection."],
        )
        verifier = FakeVerifierClient(
            response_text=load_output("valid_agree_output.json")
        )
        drafter = FakeDraftingClient(response_text=load_draft_output())

        result = run_post_decision_drafting(
            verifier_client=verifier,
            drafting_client=drafter,
            verification_request_id="verify-rejected",
            drafting_request_id="draft-rejected",
            proposed_decision=verifier_input.proposed_decision,
            evidence_bundle=verifier_input.evidence_bundle,
            deterministic_validation=rejected,
            runtime_trace=InMemoryRuntimeTrace(run_id=verifier_input.run_id),
        )

        self.assertEqual(DETERMINISTIC_VALIDATION_REJECTED, result.stop_reason)
        self.assertIsNone(result.verification_execution)
        self.assertIsNone(result.drafting_execution)
        self.assertEqual([], verifier.received_requests)
        self.assertEqual([], drafter.received_requests)

    def test_uncertain_verification_never_calls_drafter(self) -> None:
        verifier_input = load_verifier_input()
        verifier = FakeVerifierClient(
            response_text=load_output("uncertain_output.json")
        )
        drafter = FakeDraftingClient(response_text=load_draft_output())

        result = run_post_decision_drafting(
            verifier_client=verifier,
            drafting_client=drafter,
            verification_request_id="verify-uncertain",
            drafting_request_id="draft-uncertain",
            proposed_decision=verifier_input.proposed_decision,
            evidence_bundle=verifier_input.evidence_bundle,
            deterministic_validation=accepted_validation(verifier_input),
            runtime_trace=InMemoryRuntimeTrace(run_id=verifier_input.run_id),
            monotonic_clock=StepClock(1.0, 1.050),
        )

        self.assertEqual(VERIFICATION_NOT_AGREED, result.stop_reason)
        self.assertIsNotNone(result.verification_execution)
        self.assertIsNone(result.drafting_execution)
        self.assertEqual(1, len(verifier.received_requests))
        self.assertEqual([], drafter.received_requests)

    def test_malformed_draft_is_returned_only_as_rejected_validation(self) -> None:
        verifier_input = load_verifier_input()

        result = run_post_decision_drafting(
            verifier_client=FakeVerifierClient(
                response_text=load_output("valid_agree_output.json")
            ),
            drafting_client=FakeDraftingClient(response_text="not-json"),
            verification_request_id="verify-malformed-draft",
            drafting_request_id="draft-malformed",
            proposed_decision=verifier_input.proposed_decision,
            evidence_bundle=verifier_input.evidence_bundle,
            deterministic_validation=accepted_validation(verifier_input),
            runtime_trace=InMemoryRuntimeTrace(run_id=verifier_input.run_id),
        )

        self.assertIsNone(result.validated_draft)
        self.assertEqual(
            DraftingOutcome.INVALID_OUTPUT,
            result.drafting_execution.validation_result.outcome,
        )


if __name__ == "__main__":
    unittest.main()
