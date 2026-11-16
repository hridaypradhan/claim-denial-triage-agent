"""Drafting authorization and groundedness validation (#51)."""

from __future__ import annotations

import json
import re
from typing import Iterable, List, Mapping, Optional, Sequence, Set, Union

from pydantic import ValidationError

from claim_agent.contracts import (
    AgentStatus,
    AppealDraft,
    Disposition,
    DraftClaimStatement,
    DraftingInput,
    DraftingOutcome,
    DraftingValidationResult,
    ValidationOutcome,
    ValidationProblem,
    VerificationOutcome,
)

DRAFTING_REASON_ACCEPTED = "DRAFT_ACCEPTED_PENDING_REVIEW"
DRAFTING_REASON_NOT_VALIDATED = "DRAFTING_UNAUTHORIZED_VALIDATION_NOT_ACCEPTED"
DRAFTING_REASON_NOT_VERIFIED = "DRAFTING_UNAUTHORIZED_VERIFICATION_FAILED"
DRAFTING_REASON_VERIFIER_MISMATCH = "DRAFTING_UNAUTHORIZED_VERIFIER_MISMATCH"
DRAFTING_REASON_DISALLOWED_DISPOSITION = "DRAFTING_UNAUTHORIZED_DISPOSITION"
DRAFTING_REASON_UNRESOLVED_CITATIONS = "DRAFTING_UNRESOLVED_CITATIONS"
DRAFTING_REASON_UNGROUNDED_CLAIM = "DRAFTING_UNGROUNDED_CLAIM"
DRAFTING_REASON_UNGROUNDED_BODY = "DRAFTING_UNGROUNDED_BODY_ASSERTION"
DRAFTING_REASON_INVALID_OUTPUT = "DRAFTING_INVALID_OUTPUT"
DRAFTING_REASON_PROVIDER_FAILURE = "DRAFTING_PROVIDER_FAILURE"
DRAFTING_REASON_TIMEOUT = "DRAFTING_TIMEOUT"

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "per",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)
_CITATION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+@v[A-Za-z0-9._-]+#[A-Za-z0-9_-]+")
_WORD_PATTERN = re.compile(r"[a-z0-9]+")


def render_grounded_appeal_body(
    grounded_claims: Sequence[DraftClaimStatement],
) -> str:
    """Deterministically render an appeal body exclusively from validated claims."""

    rendered_sentences: List[str] = []
    for claim in grounded_claims:
        stmt = claim.statement.strip()
        if not stmt.endswith((".", "!", "?")):
            stmt += "."
        citations = ", ".join(claim.citation_ids)
        rendered_sentences.append(f"{stmt} [{citations}]")
    return " ".join(rendered_sentences)


def check_drafting_authorization(
    drafting_input: DraftingInput,
) -> Optional[DraftingValidationResult]:
    """Return a rejection result if drafting is not authorized to begin, else None."""

    val = drafting_input.validation_result
    ver = drafting_input.verifier_result
    bundle = drafting_input.evidence_bundle
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

    # Require typed AGREE result from the tool-less verifier (#50)
    if (
        ver.outcome != VerificationOutcome.AGREE
        or ver.requires_manual_review
        or ver.validated_report is None
    ):
        return _reject(
            base_kwargs,
            DraftingOutcome.REJECTED_UNAUTHORIZED,
            DRAFTING_REASON_NOT_VERIFIED,
            "Drafting is prohibited unless tool-less evidence verification returned "
            "AGREE with requires_manual_review=False.",
        )

    # Require exact decision and canonical bundle provenance match
    if (
        ver.decision_id != decision.decision_id
        or ver.claim_id != decision.claim_id
        or ver.service_line_id != decision.service_line_id
        or ver.validated_report.decision_id != decision.decision_id
        or ver.validated_report.claim_id != decision.claim_id
        or ver.validated_report.service_line_id != decision.service_line_id
    ):
        return _reject(
            base_kwargs,
            DraftingOutcome.REJECTED_UNAUTHORIZED,
            DRAFTING_REASON_VERIFIER_MISMATCH,
            "Verifier result decision/claim identity does not match the validated "
            "decision.",
        )

    if (
        ver.run_id != bundle.run_id
        or ver.knowledge_snapshot_id != bundle.knowledge_snapshot_id
        or ver.bundle_fingerprint != bundle.fingerprint()
    ):
        return _reject(
            base_kwargs,
            DraftingOutcome.REJECTED_UNAUTHORIZED,
            DRAFTING_REASON_VERIFIER_MISMATCH,
            "Verifier result canonical bundle provenance does not match "
            "drafting_input.evidence_bundle.",
        )

    if not bundle.passages:
        return _reject(
            base_kwargs,
            DraftingOutcome.REJECTED_UNAUTHORIZED,
            DRAFTING_REASON_UNRESOLVED_CITATIONS,
            "Drafting requires same-run retrieved passages in evidence_bundle.",
        )

    decision_citation_problems = bundle.check_citations(decision.citation_ids)
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
    passage_text_by_id = {
        bp.passage.passage_id: bp.passage.text for bp in bundle.passages
    }
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

        # Verify that the asserted claim content is actually supported by the
        # text of its cited same-run passages (not merely accompanied by a valid ID).
        cited_texts = [
            passage_text_by_id[cid]
            for cid in claim_stmt.citation_ids
            if cid in passage_text_by_id
        ]
        if cited_texts and not _is_supported_by_texts(
            claim_stmt.statement, cited_texts
        ):
            problems.append(
                ValidationProblem(
                    code=DRAFTING_REASON_UNGROUNDED_CLAIM,
                    field_path=f"grounded_claims[{idx}].statement",
                    message=(
                        f"Claim statement '{claim_stmt.statement}' is not factually "
                        "supported by its cited passage text(s)."
                    ),
                    is_repairable=False,
                )
            )

    # Verify that every sentence/assertion in draft.body is represented by the
    # structured grounded_claims (and cites only allowed draft citations).
    body_citations = _CITATION_ID_PATTERN.findall(draft.body)
    for cid in body_citations:
        if cid not in top_level_citations:
            problems.append(
                ValidationProblem(
                    code=DRAFTING_REASON_UNGROUNDED_BODY,
                    field_path="body",
                    message=(
                        f"Body cites '{cid}' which is absent from draft citation_ids."
                    ),
                    is_repairable=False,
                )
            )

    claim_texts = [c.statement for c in draft.grounded_claims]
    for s_idx, sentence in enumerate(_split_sentences(draft.body)):
        if not _is_supported_by_texts(sentence, claim_texts):
            problems.append(
                ValidationProblem(
                    code=DRAFTING_REASON_UNGROUNDED_BODY,
                    field_path=f"body.sentence[{s_idx}]",
                    message=(
                        f"Body assertion '{sentence}' is not represented in "
                        "grounded_claims."
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


def _split_sentences(text: str) -> List[str]:
    stripped_citations = _CITATION_ID_PATTERN.sub(" ", text)
    parts = re.split(r"[.!?]+(?:\s+|$)", stripped_citations)
    return [part.strip() for part in parts if part.strip()]


def _content_tokens(text: str) -> Set[str]:
    cleaned = _CITATION_ID_PATTERN.sub(" ", text.lower())
    return {
        tok
        for tok in _WORD_PATTERN.findall(cleaned)
        if tok not in _STOPWORDS and (len(tok) > 2 or tok.isdigit())
    }


def _is_supported_by_texts(assertion: str, source_texts: Iterable[str]) -> bool:
    assertion_tokens = _content_tokens(assertion)
    if not assertion_tokens:
        return False
    source_tokens: Set[str] = set()
    for text in source_texts:
        source_tokens |= _content_tokens(text)
    if not source_tokens:
        return False

    # Every numeric quantity (e.g., "6" weeks) in the assertion must appear in
    # the supporting source text(s).
    assertion_numbers = {tok for tok in assertion_tokens if tok.isdigit()}
    if not assertion_numbers.issubset(source_tokens):
        return False

    overlap = assertion_tokens & source_tokens
    return (len(overlap) / len(assertion_tokens)) >= 0.6


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