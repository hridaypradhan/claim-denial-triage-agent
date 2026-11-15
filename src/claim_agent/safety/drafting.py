"""Drafting authorization and groundedness validation (#51)."""

from __future__ import annotations

import json
from typing import List, Mapping, Optional, Union

from pydantic import ValidationError

from claim_agent.contracts import (
    AgentStatus,
    AppealDraft,
    Disposition,
    DraftingInput,
    DraftingOutcome,
    DraftingValidationResult,
    ValidationOutcome,
    ValidationProblem,
)

DRAFTING_REASON_ACCEPTED = "DRAFT_ACCEPTED_PENDING_REVIEW"
DRAFTING_REASON_NOT_VALIDATED = "DRAFTING_UNAUTHORIZED_VALIDATION_NOT_ACCEPTED"
DRAFTING_REASON_NOT_VERIFIED = "DRAFTING_UNAUTHORIZED_VERIFICATION_FAILED"
DRAFTING_REASON_DISALLOWED_DISPOSITION = "DRAFTING_UNAUTHORIZED_DISPOSITION"
DRAFTING_REASON_UNRESOLVED_CITATIONS = "DRAFTING_UNRESOLVED_CITATIONS"
DRAFTING_REASON_UNGROUNDED_CLAIM = "DRAFTING_UNGROUNDED_CLAIM"
DRAFTING_REASON_INVALID_OUTPUT = "DRAFTING_INVALID_OUTPUT"
DRAFTING_REASON_PROVIDER_FAILURE = "DRAFTING_PROVIDER_FAILURE"
DRAFTING_REASON_TIMEOUT = "DRAFTING_TIMEOUT"


def check_drafting_authorization(
    drafting_input: DraftingInput,
) -> Optional[DraftingValidationResult]:
    """Return a rejection result if drafting is not authorized to begin, else None."""

    val = drafting_input.validation_result
    base_kwargs = {
        "decision_id": val.decision_id,
        "claim_id": val.claim_id,
        "service_line_id": val.service_line_id,
    }

    if val.outcome != ValidationOutcome.ACCEPTED or val.validated_decision is None:
        return _reject(
            base_kwargs,
            DraftingOutcome.REJECTED_UNAUTHORIZED,
            DRAFTING_REASON_NOT_VALIDATED,
            "Drafting is prohibited unless deterministic validation is ACCEPTED.",
        )

    decision = val.validated_decision
    if decision.agent_status == AgentStatus.MANUAL_REVIEW_REQUIRED:
        return _reject(
            base_kwargs,
            DraftingOutcome.REJECTED_UNAUTHORIZED,
            DRAFTING_REASON_NOT_VALIDATED,
            "Drafting is prohibited for decisions requiring manual review.",
        )

    if decision.recommended_disposition != Disposition.APPEAL_WITH_DOCUMENTATION:
        return _reject(
            base_kwargs,
            DraftingOutcome.REJECTED_UNAUTHORIZED,
            DRAFTING_REASON_DISALLOWED_DISPOSITION,
            (
                "Drafting is only permitted for APPEAL_WITH_DOCUMENTATION, got "
                f"{decision.recommended_disposition.value}."
            ),
        )

    if not drafting_input.verification_agreed:
        return _reject(
            base_kwargs,
            DraftingOutcome.REJECTED_UNAUTHORIZED,
            DRAFTING_REASON_NOT_VERIFIED,
            "Drafting is prohibited before tool-less evidence verification agrees.",
        )

    if not drafting_input.evidence_bundle.passages:
        return _reject(
            base_kwargs,
            DraftingOutcome.REJECTED_UNAUTHORIZED,
            DRAFTING_REASON_UNRESOLVED_CITATIONS,
            "Drafting requires same-run retrieved passages in evidence_bundle.",
        )

    decision_citation_problems = drafting_input.evidence_bundle.check_citations(
        decision.citation_ids
    )
    if decision_citation_problems:
        return _reject(
            base_kwargs,
            DraftingOutcome.REJECTED_UNAUTHORIZED,
            DRAFTING_REASON_UNRESOLVED_CITATIONS,
            "Validated decision citations failed same-run bundle resolution: "
            + "; ".join(p.message for p in decision_citation_problems),
        )

    return None


def validate_appeal_draft(
    drafting_input: DraftingInput,
    *,
    raw_output: Optional[Union[str, Mapping[str, object]]] = None,
    provider_error: Optional[str] = None,
    timed_out: bool = False,
) -> DraftingValidationResult:
    """Validate pre-flight drafting authorization and post-generation groundedness."""

    unauthorized = check_drafting_authorization(drafting_input)
    if unauthorized is not None:
        return unauthorized

    decision = drafting_input.validation_result.validated_decision
    assert decision is not None
    base_kwargs = {
        "decision_id": decision.decision_id,
        "claim_id": decision.claim_id,
        "service_line_id": decision.service_line_id,
    }

    if timed_out:
        return _reject(
            base_kwargs,
            DraftingOutcome.TIMEOUT,
            DRAFTING_REASON_TIMEOUT,
            "Appeal drafting timed out; escalating to manual review.",
        )

    if provider_error:
        return _reject(
            base_kwargs,
            DraftingOutcome.PROVIDER_FAILURE,
            DRAFTING_REASON_PROVIDER_FAILURE,
            f"Appeal drafting provider failure: {provider_error}",
        )

    if raw_output is None:
        return _reject(
            base_kwargs,
            DraftingOutcome.INVALID_OUTPUT,
            DRAFTING_REASON_INVALID_OUTPUT,
            "Appeal drafting returned empty output.",
        )

    try:
        payload_json = (
            raw_output
            if isinstance(raw_output, str)
            else json.dumps(dict(raw_output))
        )
        draft = AppealDraft.model_validate_json(payload_json)
    except (TypeError, ValueError, ValidationError) as exc:
        return _reject(
            base_kwargs,
            DraftingOutcome.INVALID_OUTPUT,
            DRAFTING_REASON_INVALID_OUTPUT,
            f"Appeal draft failed schema validation: {exc}",
        )

    if (
        draft.decision_id != decision.decision_id
        or draft.claim_id != decision.claim_id
        or draft.service_line_id != decision.service_line_id
    ):
        return _reject(
            base_kwargs,
            DraftingOutcome.INVALID_OUTPUT,
            DRAFTING_REASON_INVALID_OUTPUT,
            "Appeal draft identity does not match the validated decision.",
        )

    bundle = drafting_input.evidence_bundle
    problems: List[ValidationProblem] = []

    for problem in bundle.check_citations(draft.citation_ids):
        problems.append(
            ValidationProblem(
                code=DRAFTING_REASON_UNRESOLVED_CITATIONS,
                field_path="citation_ids",
                message=problem.message,
                is_repairable=False,
            )
        )

    top_level_citations = set(draft.citation_ids)
    for idx, claim_stmt in enumerate(draft.grounded_claims):
        for problem in bundle.check_citations(claim_stmt.citation_ids):
            problems.append(
                ValidationProblem(
                    code=DRAFTING_REASON_UNGROUNDED_CLAIM,
                    field_path=f"grounded_claims[{idx}].citation_ids",
                    message=problem.message,
                    is_repairable=False,
                )
            )
        for cid in claim_stmt.citation_ids:
            if cid not in top_level_citations:
                problems.append(
                    ValidationProblem(
                        code=DRAFTING_REASON_UNGROUNDED_CLAIM,
                        field_path=f"grounded_claims[{idx}].citation_ids",
                        message=(
                            f"Claim citation '{cid}' is missing from draft citation_ids."
                        ),
                        is_repairable=False,
                    )
                )

    if problems:
        return DraftingValidationResult(
            **base_kwargs,
            outcome=DraftingOutcome.REJECTED_UNGROUNDED,
            requires_manual_review=True,
            approval_state="PENDING_REVIEW",
            reason_code=DRAFTING_REASON_UNGROUNDED_CLAIM,
            validation_problems=problems,
        )

    return DraftingValidationResult(
        **base_kwargs,
        outcome=DraftingOutcome.ACCEPTED,
        requires_manual_review=False,
        approval_state="PENDING_REVIEW",
        reason_code=DRAFTING_REASON_ACCEPTED,
        validated_draft=draft,
    )


def _reject(
    base_kwargs: Mapping[str, object],
    outcome: DraftingOutcome,
    reason_code: str,
    message: str,
) -> DraftingValidationResult:
    return DraftingValidationResult(
        **base_kwargs,
        outcome=outcome,
        requires_manual_review=True,
        approval_state="PENDING_REVIEW",
        reason_code=reason_code,
        validation_problems=[
            ValidationProblem(
                code=reason_code,
                field_path="drafting",
                message=message,
                is_repairable=False,
            )
        ],
    )