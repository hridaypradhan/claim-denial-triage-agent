"""Coordinate post-decision verification and grounded appeal drafting.

The coordinator enforces the gate order required for an appeal draft:

1. deterministic validation must accept the exact proposed decision;
2. the tool-less verifier must agree with that decision and evidence;
3. the drafting model may then receive the authorized DraftingInput;
4. deterministic drafting validation decides whether a draft is exposed.

No rejected or manual-review result falls back to another model or to scripted
draft generation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from claim_agent.agent.drafting.execution import (
    DraftingExecutionResult,
    execute_drafting,
)
from claim_agent.agent.drafting.interface import DraftingClient
from claim_agent.agent.drafting.trace import RuntimeTraceRecorder
from claim_agent.agent.verification.execution import (
    VerifierExecutionResult,
    execute_verification,
)
from claim_agent.agent.verification.interface import VerifierClient
from claim_agent.contracts import (
    AgentDecision,
    AppealDraft,
    DraftingInput,
    EvidenceBundle,
    ValidationOutcome,
    ValidationResult,
    VerificationOutcome,
)

DETERMINISTIC_VALIDATION_REJECTED = "deterministic_validation_rejected"
VERIFICATION_NOT_AGREED = "verification_not_agreed"
DRAFTING_ATTEMPTED = "drafting_attempted"


@dataclass(frozen=True)
class PostDecisionDraftingResult:
    """Observable results from the post-decision safety gates."""

    stop_reason: str
    verification_execution: VerifierExecutionResult | None
    drafting_execution: DraftingExecutionResult | None

    @property
    def validated_draft(self) -> AppealDraft | None:
        """Expose only a draft accepted by deterministic validation."""

        if self.drafting_execution is None:
            return None

        return self.drafting_execution.validation_result.validated_draft


def run_post_decision_drafting(
    *,
    verifier_client: VerifierClient,
    drafting_client: DraftingClient,
    verification_request_id: str,
    drafting_request_id: str,
    proposed_decision: AgentDecision,
    evidence_bundle: EvidenceBundle,
    deterministic_validation: ValidationResult,
    runtime_trace: RuntimeTraceRecorder,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> PostDecisionDraftingResult:
    """Run verification and drafting only after each prior gate succeeds."""

    if not _deterministic_validation_authorizes_verification(
        proposed_decision=proposed_decision,
        deterministic_validation=deterministic_validation,
    ):
        return PostDecisionDraftingResult(
            stop_reason=DETERMINISTIC_VALIDATION_REJECTED,
            verification_execution=None,
            drafting_execution=None,
        )

    verification_execution = execute_verification(
        verifier_client=verifier_client,
        request_id=verification_request_id,
        proposed_decision=proposed_decision,
        evidence_bundle=evidence_bundle,
        deterministic_validation=deterministic_validation,
        runtime_trace=runtime_trace,
        monotonic_clock=monotonic_clock,
    )

    verifier_result = verification_execution.validation_result
    if (
        verifier_result.outcome != VerificationOutcome.AGREE
        or verifier_result.requires_manual_review
    ):
        return PostDecisionDraftingResult(
            stop_reason=VERIFICATION_NOT_AGREED,
            verification_execution=verification_execution,
            drafting_execution=None,
        )

    drafting_input = DraftingInput(
        validation_result=deterministic_validation,
        verifier_result=verifier_result,
        evidence_bundle=evidence_bundle,
    )
    drafting_execution = execute_drafting(
        drafting_client=drafting_client,
        request_id=drafting_request_id,
        drafting_input=drafting_input,
        runtime_trace=runtime_trace,
        monotonic_clock=monotonic_clock,
    )

    return PostDecisionDraftingResult(
        stop_reason=DRAFTING_ATTEMPTED,
        verification_execution=verification_execution,
        drafting_execution=drafting_execution,
    )


def _deterministic_validation_authorizes_verification(
    *,
    proposed_decision: AgentDecision,
    deterministic_validation: ValidationResult,
) -> bool:
    """Require acceptance of the exact decision before model verification."""

    return (
        deterministic_validation.outcome == ValidationOutcome.ACCEPTED
        and deterministic_validation.validated_decision == proposed_decision
        and deterministic_validation.decision_id == proposed_decision.decision_id
        and deterministic_validation.claim_id == proposed_decision.claim_id
        and deterministic_validation.service_line_id
        == proposed_decision.service_line_id
    )
