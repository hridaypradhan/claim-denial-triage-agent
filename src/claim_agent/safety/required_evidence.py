"""Deterministic required-evidence safety decisions (#49).

Distinguishes decision-critical evidence from fields supplied by a proposed
correction itself and helpful-but-nonessential documents, returning a stable
safety decision that routing (#57) can consume without overriding normal
DETERMINISTIC_ONLY vs. AGENT_REQUIRED route selection.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional, Sequence

from claim_agent.contracts.schema import (
    BaselineFinding,
    ClassifiedEvidenceRequirement,
    Disposition,
    EvidenceRequirementCategory,
    RequiredEvidenceSafetyDecision,
    RouteOutcome,
)

# Stable reason codes consumed by routing (#57) and audit traces
REASON_NO_REQUIRED_EVIDENCE = "NO_REQUIRED_EVIDENCE"
REASON_DECISION_CRITICAL_SATISFIED = "DECISION_CRITICAL_EVIDENCE_SATISFIED"
REASON_MISSING_DECISION_CRITICAL = "MISSING_DECISION_CRITICAL_EVIDENCE"
REASON_CORRECTION_INPUTS_ONLY = "CORRECTION_SUPPLIED_INPUTS_ONLY"
REASON_NONESSENTIAL_ONLY = "NONESSENTIAL_EVIDENCE_ONLY"

# Claim-form fields supplied directly by a proposed correction action
CORRECTION_SUPPLYABLE_FIELDS = frozenset(
    {
        "procedure_code",
        "diagnosis_code",
        "referring_provider_npi",
        "rendering_provider_npi",
        "modifier",
        "modifiers",
        "prior_auth_number",
        "place_of_service",
        "units",
    }
)

# Helpful contextual/reference artifacts that do not block proceeding
HELPFUL_NONESSENTIAL_EVIDENCE = frozenset(
    {
        "updated_insurance_card",
        "plan_benefit_reference",
        "supplemental_notes",
        "informational_reference",
    }
)

# External clinical, payer, or lookup artifacts required before acting
DECISION_CRITICAL_EVIDENCE = frozenset(
    {
        "ncci_edit_lookup",
        "primary_eob",
        "cob_confirmation",
        "clinical_note",
        "medical_records",
        "medical_record",
        "operative_report",
        "urgency_justification",
        "auth_requirement_grid",
        "835_remittance",
        "prior_claim_history",
        "clearinghouse_acceptance_report",
        "eligibility_response",
        "carc_lookup",
    }
)


def classify_evidence_requirement(
    requirement: str,
    *,
    available_evidence: Iterable[str] = (),
    correction_inputs: Optional[Mapping[str, object]] = None,
    nonessential_overrides: Iterable[str] = (),
) -> ClassifiedEvidenceRequirement:
    """Classify one required-evidence token into its deterministic safety category."""

    normalized = requirement.strip().lower()
    available_set = {item.strip().lower() for item in available_evidence}
    nonessential_set = HELPFUL_NONESSENTIAL_EVIDENCE | {
        item.strip().lower() for item in nonessential_overrides
    }
    normalized_correction_map = {
        key.strip().lower(): value for key, value in (correction_inputs or {}).items()
    }

    # 1. Helpful-but-nonessential documents never force manual review.
    if normalized in nonessential_set:
        is_supplied = normalized in available_set
        return ClassifiedEvidenceRequirement(
            requirement=requirement,
            category=EvidenceRequirementCategory.HELPFUL_NONESSENTIAL,
            satisfied=True,
            supplied=is_supplied,
            reason=(
                f"'{requirement}' is helpful context rather than decision-critical "
                "evidence."
            ),
        )

    # 2. Fields supplied by the proposed correction itself do not require external
    # retrieval to avoid manual review. When correction_inputs is omitted or
    # empty, the field remains expected from the correction action (supplied=False)
    # without falsely marking it as already supplied.
    if normalized in CORRECTION_SUPPLYABLE_FIELDS:
        has_key = normalized in normalized_correction_map
        val = normalized_correction_map.get(normalized)
        is_explicitly_blank = has_key and (val is None or val == "" or val == [])
        is_supplied = has_key and not is_explicitly_blank
        if is_explicitly_blank:
            return ClassifiedEvidenceRequirement(
                requirement=requirement,
                category=EvidenceRequirementCategory.CORRECTION_INPUT,
                satisfied=False,
                supplied=False,
                reason=(
                    f"Correction input '{requirement}' was provided with an empty value."
                ),
            )
        reason = (
            f"'{requirement}' was supplied in correction_inputs."
            if is_supplied
            else (
                f"'{requirement}' is expected from the proposed claim correction "
                "rather than external evidence."
            )
        )
        return ClassifiedEvidenceRequirement(
            requirement=requirement,
            category=EvidenceRequirementCategory.CORRECTION_INPUT,
            satisfied=True,
            supplied=is_supplied,
            reason=reason,
        )

    # 3. Decision-critical evidence (known external artifacts, or any unknown
    # requirement token by fail-closed default) must be present in available
    # evidence.
    is_present = normalized in available_set
    if normalized in DECISION_CRITICAL_EVIDENCE:
        reason = (
            f"Decision-critical evidence '{requirement}' is present."
            if is_present
            else f"Decision-critical evidence '{requirement}' is missing."
        )
    else:
        reason = (
            f"Required evidence '{requirement}' is present."
            if is_present
            else (
                f"Unclassified required evidence '{requirement}' is missing "
                "(fail-closed as decision-critical)."
            )
        )

    return ClassifiedEvidenceRequirement(
        requirement=requirement,
        category=EvidenceRequirementCategory.DECISION_CRITICAL,
        satisfied=is_present,
        supplied=is_present,
        reason=reason,
    )


def evaluate_required_evidence_safety(
    finding: BaselineFinding,
    *,
    available_evidence: Sequence[str] = (),
    correction_inputs: Optional[Mapping[str, object]] = None,
    nonessential_evidence: Sequence[str] = (),
) -> RequiredEvidenceSafetyDecision:
    """Evaluate whether missing required evidence forces MANUAL_REVIEW.

    Rules:
    - Missing decision-critical evidence forces MANUAL_REVIEW.
    - Fields supplied by the proposed correction itself do not force MANUAL_REVIEW.
    - Helpful-but-nonessential documents (e.g., updated_insurance_card) do not
      force MANUAL_REVIEW.
    - Unknown/unrecognized requirement tokens fail closed as DECISION_CRITICAL.
    - When safe to continue, returns ``requires_manual_review=False`` and
      ``recommended_outcome=None`` so routing (#57) preserves normal
      DETERMINISTIC_ONLY vs. AGENT_REQUIRED route selection.
    """

    if finding.baseline_disposition == Disposition.MANUAL_REVIEW:
        return RequiredEvidenceSafetyDecision(
            claim_id=finding.claim_id,
            service_line_id=finding.service_line_id,
            requires_manual_review=True,
            recommended_outcome=RouteOutcome.MANUAL_REVIEW,
            reason_code=REASON_MISSING_DECISION_CRITICAL,
            rationale="Baseline finding already requires manual review.",
        )

    if not finding.required_evidence:
        return RequiredEvidenceSafetyDecision(
            claim_id=finding.claim_id,
            service_line_id=finding.service_line_id,
            requires_manual_review=False,
            recommended_outcome=None,
            reason_code=REASON_NO_REQUIRED_EVIDENCE,
            rationale="No external evidence is required; safe to continue normal routing.",
        )

    classified = [
        classify_evidence_requirement(
            req,
            available_evidence=available_evidence,
            correction_inputs=correction_inputs,
            nonessential_overrides=nonessential_evidence,
        )
        for req in finding.required_evidence
    ]

    missing_critical = [
        item.requirement
        for item in classified
        if item.category == EvidenceRequirementCategory.DECISION_CRITICAL
        and not item.satisfied
    ]
    unsatisfied_correction = [
        item.requirement
        for item in classified
        if item.category == EvidenceRequirementCategory.CORRECTION_INPUT
        and not item.satisfied
    ]
    expected_correction = [
        item.requirement
        for item in classified
        if item.category == EvidenceRequirementCategory.CORRECTION_INPUT
    ]
    supplied_correction = [
        item.requirement
        for item in classified
        if item.category == EvidenceRequirementCategory.CORRECTION_INPUT
        and item.supplied
    ]
    nonessential_items = [
        item.requirement
        for item in classified
        if item.category == EvidenceRequirementCategory.HELPFUL_NONESSENTIAL
    ]

    if missing_critical or unsatisfied_correction:
        missing_all = missing_critical + unsatisfied_correction
        return RequiredEvidenceSafetyDecision(
            claim_id=finding.claim_id,
            service_line_id=finding.service_line_id,
            requires_manual_review=True,
            recommended_outcome=RouteOutcome.MANUAL_REVIEW,
            reason_code=REASON_MISSING_DECISION_CRITICAL,
            rationale=(
                "Missing decision-critical evidence requires manual review: "
                + ", ".join(missing_all)
            ),
            classified_requirements=classified,
            missing_critical_evidence=missing_all,
            expected_correction_inputs=expected_correction,
            supplied_correction_inputs=supplied_correction,
            nonessential_evidence=nonessential_items,
        )

    has_critical_satisfied = any(
        item.category == EvidenceRequirementCategory.DECISION_CRITICAL
        for item in classified
    )
    if has_critical_satisfied:
        reason_code = REASON_DECISION_CRITICAL_SATISFIED
        rationale = (
            "All decision-critical evidence requirements are satisfied; "
            "safe to continue normal routing."
        )
    elif expected_correction:
        reason_code = REASON_CORRECTION_INPUTS_ONLY
        rationale = (
            "Required fields are supplied by the proposed claim correction; "
            "safe to continue normal routing."
        )
    else:
        reason_code = REASON_NONESSENTIAL_ONLY
        rationale = (
            "Only helpful-but-nonessential documents were listed; "
            "safe to continue normal routing."
        )

    return RequiredEvidenceSafetyDecision(
        claim_id=finding.claim_id,
        service_line_id=finding.service_line_id,
        requires_manual_review=False,
        recommended_outcome=None,
        reason_code=reason_code,
        rationale=rationale,
        classified_requirements=classified,
        missing_critical_evidence=[],
        expected_correction_inputs=expected_correction,
        supplied_correction_inputs=supplied_correction,
        nonessential_evidence=nonessential_items,
    )