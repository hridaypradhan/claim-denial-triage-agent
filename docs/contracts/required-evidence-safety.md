# Required-Evidence Safety Decisions (`#49`)

`evaluate_required_evidence_safety` (`src/claim_agent/safety/required_evidence.py`) checks whether missing `BaselineFinding.required_evidence` forces `MANUAL_REVIEW` without overriding normal `DETERMINISTIC_ONLY` vs. `AGENT_REQUIRED` routing (`#57`).

## Evidence Requirement Categories

1. **`DECISION_CRITICAL` (fail-closed):** External clinical, payer, or lookup artifacts (`ncci_edit_lookup`, `primary_eob`, `cob_confirmation`, `clinical_note`, `medical_records`, `operative_report`, `urgency_justification`, `auth_requirement_grid`, `835_remittance`, `prior_claim_history`, `clearinghouse_acceptance_report`, `eligibility_response`, `carc_lookup`, plus any unrecognized token). If absent from `available_evidence`, returns `requires_manual_review=True` and `recommended_outcome=RouteOutcome.MANUAL_REVIEW`.
2. **`CORRECTION_INPUT`:** Claim-form fields supplied directly by a proposed correction (`rendering_provider_npi`, `procedure_code`, `diagnosis_code`, `referring_provider_npi`, `modifier`, `modifiers`, `prior_auth_number`, `place_of_service`, `units`). Recorded in `expected_correction_inputs` (and `supplied_correction_inputs` when populated in `correction_inputs`) without forcing manual review.
3. **`HELPFUL_NONESSENTIAL`:** Useful contextual documents (`updated_insurance_card`, `plan_benefit_reference`, `supplemental_notes`, `informational_reference`). Never force manual review.

## Documented Examples

| Case | Root Cause / Baseline Disposition | `required_evidence` | `available_evidence` | Safety Result | Routing Effect (`#57`) |
|---|---|---|---|---|---|
| `CLM-2026-610101-L1` | `BUNDLING_NCCI` / `CORRECT_AND_RESUBMIT` | `["ncci_edit_lookup"]` | `[]` | `requires_manual_review=True`, `recommended_outcome=MANUAL_REVIEW` (`MISSING_DECISION_CRITICAL_EVIDENCE`) | Escalates to `MANUAL_REVIEW` |
| `CLM-2026-610107-L1` | `COORDINATION_OF_BENEFITS` / `ROUTE_TO_CORRECT_PAYER` | `["primary_eob", "cob_confirmation"]` | `["primary_eob"]` | `requires_manual_review=True`, `recommended_outcome=MANUAL_REVIEW` (`MISSING_DECISION_CRITICAL_EVIDENCE`) | Escalates to `MANUAL_REVIEW` |
| `CLM-2026-610104-L1` | `REGISTRATION_ELIGIBILITY` / `ROUTE_TO_CORRECT_PAYER` | `["updated_insurance_card"]` | `[]` | `requires_manual_review=False`, `recommended_outcome=None` (`NONESSENTIAL_EVIDENCE_ONLY`) | Preserves `ROUTE_TO_CORRECT_PAYER` |
| `CLM-2026-005311-L1` | `CODING` / `CORRECT_AND_RESUBMIT` | `["rendering_provider_npi"]` | `[]` | `requires_manual_review=False`, `recommended_outcome=None` (`CORRECTION_SUPPLIED_INPUTS_ONLY`) | Preserves `CORRECT_AND_RESUBMIT` |
| `CLM-2026-610115-L1` | `CODING` / `CORRECT_AND_RESUBMIT` | `["procedure_code"]` | `[]` | `requires_manual_review=False`, `recommended_outcome=None` (`CORRECTION_SUPPLIED_INPUTS_ONLY`) | Preserves `CORRECT_AND_RESUBMIT` |
| `CLM-2026-005097-L1` | `MEDICAL_NECESSITY` / `APPEAL_WITH_DOCUMENTATION` | `["clinical_note"]` | `["clinical_note"]` | `requires_manual_review=False`, `recommended_outcome=None` (`DECISION_CRITICAL_EVIDENCE_SATISFIED`) | Continues to `AGENT_REQUIRED` |