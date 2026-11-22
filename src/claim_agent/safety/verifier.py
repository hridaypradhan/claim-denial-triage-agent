"""Deterministic fail-safe validation for the tool-less evidence verifier."""

from __future__ import annotations

import json
from typing import Mapping, Optional, Union

from pydantic import ValidationError

from claim_agent.contracts import (
    ValidationOutcome,
    ValidationProblem,
    ValidationResult,
    VerificationOutcome,
    VerifierInput,
    VerifierReport,
    VerifierValidationResult,
)
from claim_agent.safety.formula_evaluator import evaluate_criteria_formula

VERIFIER_REASON_AGREED = "VERIFIER_AGREED"
VERIFIER_REASON_DISAGREED = "VERIFIER_DISAGREED"
VERIFIER_REASON_UNCERTAIN = "VERIFIER_UNCERTAIN"
VERIFIER_REASON_INVALID_OUTPUT = "VERIFIER_INVALID_OUTPUT"
VERIFIER_REASON_PROVIDER_FAILURE = "VERIFIER_PROVIDER_FAILURE"
VERIFIER_REASON_TIMEOUT = "VERIFIER_TIMEOUT"
VERIFIER_REASON_DETERMINISTIC_REJECTED = "VERIFIER_BLOCKED_BY_DETERMINISTIC_REJECTION"
VERIFIER_REASON_VALIDATION_MISMATCH = "VERIFIER_VALIDATION_DECISION_MISMATCH"
VERIFIER_REASON_INCOHERENT_AGREE = "VERIFIER_INCOHERENT_AGREE_REPORT"
VERIFIER_REASON_FORMULA_NOT_SATISFIED = "VERIFIER_FORMULA_NOT_SATISFIED"


def validate_verifier_response(
    verifier_input: VerifierInput,
    deterministic_validation: ValidationResult,
    *,
    raw_output: Optional[Union[str, Mapping[str, object]]] = None,
    provider_error: Optional[str] = None,
    timed_out: bool = False,
) -> VerifierValidationResult:
    """Validate tool-less verifier output and enforce fail-safe escalation rules.

    Invariants:
    - Every returned VerifierValidationResult binds decision identity plus the
      originating canonical evidence bundle provenance (run_id,
      knowledge_snapshot_id, bundle_fingerprint) so results from one run or
      snapshot cannot be replayed against another bundle.
    - No verifier outcome can approve a case after deterministic rejection.
    - deterministic_validation must be ACCEPTED and belong to the exact same
      decision_id, claim_id, service_line_id, and validated_decision as
      verifier_input.proposed_decision.
    - AGREE reports cannot contain disputed criteria or unresolved questions,
      all referenced criterion IDs must resolve against proposed_decision, and
      supported_criterion_ids must satisfy evidence_bundle.policy_formula when
      a policy formula is applicable.
    - Citations must resolve cleanly via evidence_bundle.check_citations().
    - Timeouts, provider failures, malformed JSON/schema, unresolved citations,
      identity mismatches, DISAGREE, and UNCERTAIN all require manual review.
    """

    decision = verifier_input.proposed_decision
    base_kwargs = {
        "decision_id": decision.decision_id,
        "claim_id": decision.claim_id,
        "service_line_id": decision.service_line_id,
        "run_id": verifier_input.run_id,
        "knowledge_snapshot_id": verifier_input.knowledge_snapshot_id,
        "bundle_fingerprint": verifier_input.bundle_fingerprint,
    }

    # 1. Deterministic rejection is final and cannot be overridden by verification.
    if deterministic_validation.outcome != ValidationOutcome.ACCEPTED:
        return VerifierValidationResult(
            **base_kwargs,
            outcome=VerificationOutcome.INVALID_OUTPUT,
            requires_manual_review=True,
            reason_code=VERIFIER_REASON_DETERMINISTIC_REJECTED,
            validation_problems=[
                ValidationProblem(
                    code=VERIFIER_REASON_DETERMINISTIC_REJECTED,
                    field_path="deterministic_validation.outcome",
                    message=(
                        "Verifier cannot approve or override a decision that failed "
                        "deterministic safety validation."
                    ),
                    is_repairable=False,
                )
            ],
        )

    # 2. Deterministic validation must match the exact proposed decision.
    if (
        deterministic_validation.decision_id != decision.decision_id
        or deterministic_validation.claim_id != decision.claim_id
        or deterministic_validation.service_line_id != decision.service_line_id
        or deterministic_validation.validated_decision != decision
    ):
        return VerifierValidationResult(
            **base_kwargs,
            outcome=VerificationOutcome.INVALID_OUTPUT,
            requires_manual_review=True,
            reason_code=VERIFIER_REASON_VALIDATION_MISMATCH,
            validation_problems=[
                ValidationProblem(
                    code=VERIFIER_REASON_VALIDATION_MISMATCH,
                    field_path="deterministic_validation.validated_decision",
                    message=(
                        "Deterministic validation result does not match the verifier "
                        "input's proposed decision."
                    ),
                    is_repairable=False,
                )
            ],
        )

    # 3. Transport timeout and provider failure fail closed to manual review.
    if timed_out:
        return VerifierValidationResult(
            **base_kwargs,
            outcome=VerificationOutcome.TIMEOUT,
            requires_manual_review=True,
            reason_code=VERIFIER_REASON_TIMEOUT,
            validation_problems=[
                ValidationProblem(
                    code=VERIFIER_REASON_TIMEOUT,
                    field_path="verifier",
                    message="Evidence verification timed out; escalating to manual review.",
                    is_repairable=False,
                )
            ],
        )

    if provider_error:
        return VerifierValidationResult(
            **base_kwargs,
            outcome=VerificationOutcome.PROVIDER_FAILURE,
            requires_manual_review=True,
            reason_code=VERIFIER_REASON_PROVIDER_FAILURE,
            validation_problems=[
                ValidationProblem(
                    code=VERIFIER_REASON_PROVIDER_FAILURE,
                    field_path="verifier",
                    message=f"Evidence verification provider failure: {provider_error}",
                    is_repairable=False,
                )
            ],
        )

    # 4. Parse and strictly validate structured verifier output.
    if raw_output is None:
        return _invalid_output(
            base_kwargs, "Verifier returned empty output; escalating to manual review."
        )

    try:
        payload_json = (
            raw_output
            if isinstance(raw_output, str)
            else json.dumps(dict(raw_output))
        )
        report = VerifierReport.model_validate_json(payload_json)
    except (TypeError, ValueError, ValidationError) as exc:
        return _invalid_output(
            base_kwargs, f"Verifier output failed schema validation: {exc}"
        )

    # 5. Enforce identity, criterion resolution, and same-run bundle citation checks.
    if (
        report.decision_id != decision.decision_id
        or report.claim_id != decision.claim_id
        or report.service_line_id != decision.service_line_id
    ):
        return _invalid_output(
            base_kwargs,
            "Verifier report identity does not match the proposed decision.",
        )

    known_criterion_ids = {
        c.criterion_id for c in decision.criterion_assessments
    }
    referenced_criteria = set(report.supported_criterion_ids) | set(
        report.disputed_criterion_ids
    )
    unknown_criteria = sorted(referenced_criteria - known_criterion_ids)
    if unknown_criteria:
        return _invalid_output(
            base_kwargs,
            "Verifier referenced unknown criterion IDs not present in proposed "
            "decision: " + ", ".join(unknown_criteria),
        )

    citation_problems = verifier_input.evidence_bundle.check_citations(
        report.citation_ids
    )
    if citation_problems:
        return _invalid_output(
            base_kwargs,
            "Verifier citations failed same-run bundle resolution: "
            + "; ".join(p.message for p in citation_problems),
        )

    # 6. Enforce outcome coherence and policy_formula coverage on AGREE reports.
    if report.outcome == VerificationOutcome.AGREE:
        if report.disputed_criterion_ids or report.unresolved_questions:
            return VerifierValidationResult(
                **base_kwargs,
                outcome=VerificationOutcome.INVALID_OUTPUT,
                requires_manual_review=True,
                reason_code=VERIFIER_REASON_INCOHERENT_AGREE,
                validation_problems=[
                    ValidationProblem(
                        code=VERIFIER_REASON_INCOHERENT_AGREE,
                        field_path="outcome",
                        message=(
                            "AGREE report cannot include disputed_criterion_ids or "
                            "unresolved_questions; escalating to manual review."
                        ),
                        is_repairable=False,
                    )
                ],
            )
        if not report.citation_ids:
            return _invalid_output(
                base_kwargs,
                "Verifier outcome AGREE requires at least one same-run citation.",
            )

        policy_formula = verifier_input.evidence_bundle.policy_formula
        if policy_formula is not None:
            if not evaluate_criteria_formula(
                policy_formula, set(report.supported_criterion_ids)
            ):
                return VerifierValidationResult(
                    **base_kwargs,
                    outcome=VerificationOutcome.INVALID_OUTPUT,
                    requires_manual_review=True,
                    reason_code=VERIFIER_REASON_FORMULA_NOT_SATISFIED,
                    validation_problems=[
                        ValidationProblem(
                            code=VERIFIER_REASON_FORMULA_NOT_SATISFIED,
                            field_path="supported_criterion_ids",
                            message=(
                                "Verifier AGREE supported_criterion_ids "
                                f"{report.supported_criterion_ids} do not satisfy "
                                f"policy formula '{policy_formula}'."
                            ),
                            is_repairable=False,
                        )
                    ],
                )

    # 7. Map structured outcomes to deterministic manual-review requirements.
    if report.outcome == VerificationOutcome.DISAGREE:
        return VerifierValidationResult(
            **base_kwargs,
            outcome=VerificationOutcome.DISAGREE,
            requires_manual_review=True,
            reason_code=VERIFIER_REASON_DISAGREED,
            validated_report=report,
        )

    if report.outcome == VerificationOutcome.UNCERTAIN:
        return VerifierValidationResult(
            **base_kwargs,
            outcome=VerificationOutcome.UNCERTAIN,
            requires_manual_review=True,
            reason_code=VERIFIER_REASON_UNCERTAIN,
            validated_report=report,
        )

    return VerifierValidationResult(
        **base_kwargs,
        outcome=VerificationOutcome.AGREE,
        requires_manual_review=False,
        reason_code=VERIFIER_REASON_AGREED,
        validated_report=report,
    )


def _invalid_output(
    base_kwargs: Mapping[str, object],
    message: str,
) -> VerifierValidationResult:
    return VerifierValidationResult(
        **base_kwargs,
        outcome=VerificationOutcome.INVALID_OUTPUT,
        requires_manual_review=True,
        reason_code=VERIFIER_REASON_INVALID_OUTPUT,
        validation_problems=[
            ValidationProblem(
                code=VERIFIER_REASON_INVALID_OUTPUT,
                field_path="verifier_output",
                message=message,
                is_repairable=False,
            )
        ],
    )
