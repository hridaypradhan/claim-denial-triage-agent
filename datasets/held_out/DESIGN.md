# Held-out case design

- **Status:** Frozen and used by `final-heldout-v1`.
- **Rubric:** v0.1 (frozen). **As-of date:** 2026-10-30.
- **Knowledge snapshot:** `knowledge-v2`.
- **Review:** Evidence feasibility, safety expectations, and all label batches
  were reviewed before final predictions were inspected.

## Size

The project plan asks for **80 held-out cases**
([team development guide](../../docs/team-development-guide.md), dataset rule
3). This set has 80 cases from 77 synthetic claims, because three claims have
two denied lines. As in development, a case is one denied service line
(rubric §1).

## Rules for this split

- Every family ID starts with `H-`, and no family, claim ID, or procedure-policy pair is shared with development. A test enforces the IDs.
- Claim IDs are `CLM-2026-7101xx` (batch A), `7102xx` (batch B), and `7103xx` (batch C). File and family names don't reveal the expected answer.
- Held-out cases are **never** used for development checks, prompt writing, or debugging (rubric §8 and §10). Running the code on them before the freeze is limited to checking that they load.
- Every held-out failure is kept for final analysis. Nothing is tuned against it.

## Batches

The batches match the frozen label-review batches and the order in which
evidence specifications were prepared.

| Batch | Cases | Focus | Label reviewer | Needs new evidence |
|---|---:|---|---|---|
| A | 28 | Medical necessity on procedures not in development: lumbar MRI, sleep tests, knee MRI across two policy versions, vitamin D frequency | Hriday | Yes: 5 policy versions, about 30 notes |
| B | 27 | Rule and safety cases decided from the claim record: authorization, bundling with no modifier, COB, wrong payer, eligibility, timely filing, coding, non-covered, contractual, unmapped code, unknown payer, duplicate | Neha | No documents |
| C | 25 | Evidence edge cases: urgent-imaging authorization exception, modifier 25 bundling disputes, CT sinus, conflicts, instruction-like text, unavailable and decoy evidence, a service date before a policy starts | Silas | Yes: 3 policies, about 30 notes |

## Coverage

| What is covered | Cases |
|---|---:|
| Evidence-dependent (a policy formula decides) | 51 |
| Decided from the claim record | 29 |
| All formula criteria documented | 12 |
| A criterion explicitly ruled out | 12 |
| A criterion never mentioned (silence) | 7 |
| Two sources disagree | 6 |
| No applicable policy: wrong payer, wrong code, or service date before the policy | 5 |
| Policy version or effective-date boundary | 8 |
| Appeal or corrected-claim window already closed | 5 |
| Missing evidence that a deterministic answer depends on | 8 |
| Patient billing: waivers, PR groups, CO groups without a waiver | 9 |
| Instruction-like text in a note | 3 |
| Multi-line claims | 6 cases on 3 claims |
| Intentionally unavailable note, and a decoy note for another claim | 2 |

Payers: UHC 26, Aetna 21, Cigna 14, BCBS AZ 14, Medicare Part B 4, and one payer missing from the contract matrix. Group codes: CO 77, PR 3. There are 17 different group-code and CARC pairs, including one unmapped CARC (234).

Every manual-review reason in rubric §4 and every disposition in rubric §2 is
reachable. Exact gold answers are stored in the separately frozen label files,
not in this design document.

## Families

| Family | Cases | What varies |
|---|---:|---|
| `H-LSPINE-1` to `-8` | 12 | UHC lumbar MRI: conservative-therapy branch vs red-flag branch, silence, conflicts, closed deadline, waiver, uncovered thoracic code, multi-line, service before the policy |
| `H-PSG-1` to `-4`, `H-HSAT-1` | 8 | Cigna sleep testing: home test inadequate vs not tried vs not mentioned, instruction text, Aetna claim with no Aetna policy, conflicting symptoms |
| `H-KNEE-1` to `-3` | 7 | Aetna knee MRI v1 (to 2026-06-30) vs v2 (adds conservative therapy): boundary pair on 06-30 and 07-01, stale X-ray, decoy note |
| `H-VITD-1`, `-2` | 3 | Medicare vitamin D repeat-test frequency (CO-151), including a signed ABN |
| `H-AUTH-URGENT-1` to `-5` | 7 | UHC urgent-imaging exception to prior authorization: notified vs not vs unknown, routine vs urgent, conflict, closed deadline, Cigna with no policy |
| `H-MOD25-1` to `-6` | 10 | Aetna modifier 25 E/M bundling disputes: separate problem vs pre-procedure only, no E/M note, instruction text, BCBS with no policy, unavailable note, closed deadline, multi-line |
| `H-CTSINUS-1` to `-4` | 7 | BCBS AZ CT sinus: chronic branch vs complication branch, silence, conflicting duration, instruction text, waiver |
| Batch B rule families (19) | 26 | One or two cases each; counterfactual pairs share a family (for example `H-COB` with and without a primary EOB) |

Full per-case facts for the corpus owner are in [`EVIDENCE_SPECS.md`](EVIDENCE_SPECS.md).

## Knowledge delivered

The frozen corpus contains eight policy versions across seven sources:
`POL-UHC-MRI-LSPINE` v1, `POL-CIGNA-SLEEP-TESTING` v1,
`POL-AETNA-MRI-KNEE` v1 and v2, `POL-MEDICAREB-VITD-TESTING` v1,
`POL-UHC-AUTH-ADV-IMAGING` v1, `POL-AETNA-EM-MOD25` v1, and
`POL-BCBSAZ-CT-SINUS` v1. Their criteria and formulas are locked in
`knowledge-v2`.

Batch B needs no documents. Case `CLM-2026-710313-L1` must have **no** note in the corpus. The decoy therapy note belongs to `CLM-2026-719925`, a claim that isn't in any manifest.

## Review outcomes

1. One primary labeler produced each batch and a different team member reviewed
   it against the frozen rubric, claims, and evidence. Disputes were resolved
   before system outputs were inspected.
2. The specified policies and notes were delivered in `knowledge-v2`; no held-out
   family was removed for corpus effort.
3. Required-evidence cases were checked against the deterministic safety rule.
4. Development cases remain separate from this held-out split.

## Regenerating

The claims, manifest, and evidence specs come from the case table in
[`build_cases.py`](build_cases.py). The final split is frozen; do not regenerate
it under the same version. A future revision must use a new dataset version,
then update the table and run `python datasets/held_out/build_cases.py` from the
repository root. The script records case facts only, never gold labels.
