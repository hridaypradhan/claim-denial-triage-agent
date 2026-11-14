# Held-out case design

- **Issue:** #52. **Owner:** Sachin. **Reviewers:** Silas (evidence feasibility), Neha (safety expectations).
- **Status:** draft for review. Case facts may still change after review. Labels come later, in #63.
- **Rubric:** v0.1 (frozen). **As-of date:** 2026-10-30. **Knowledge snapshot:** set in #62 when the corpus is frozen.

## Size

The project plan asks for **80 held-out cases** ([TEAM_DEVELOPMENT_GUIDE.md](../../docs/TEAM_DEVELOPMENT_GUIDE.md), dataset rule 3). This set has 80 cases from 77 synthetic claims, because three claims have two denied lines. As in development, a case is one denied service line (rubric §1).

## Rules for this split

- Every family ID starts with `H-`, and no family, claim ID, or procedure-policy pair is shared with development. A test enforces the IDs.
- Claim IDs are `CLM-2026-7101xx` (batch A), `7102xx` (batch B), and `7103xx` (batch C). File and family names don't reveal the expected answer.
- Held-out cases are **never** used for development checks, prompt writing, or debugging (rubric §8 and §10). Running the code on them before the freeze is limited to checking that they load.
- Every held-out failure is kept for the final analysis (#66). Nothing is tuned against it.

## Batches

The batches match the review batches in #63. They are also the order in which specs go to the corpus owner (#58).

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
| Missing evidence that a deterministic answer depends on (the #49 rule) | 8 |
| Patient billing: waivers, PR groups, CO groups without a waiver | 9 |
| Instruction-like text in a note | 3 |
| Multi-line claims | 6 cases on 3 claims |
| Intentionally unavailable note, and a decoy note for another claim | 2 |

Payers: UHC 26, Aetna 21, Cigna 14, BCBS AZ 14, Medicare Part B 4, and one payer missing from the contract matrix. Group codes: CO 77, PR 3. There are 17 different group-code and CARC pairs, including one unmapped CARC (234).

Every manual-review reason in rubric §4 and every disposition in rubric §2 is reachable. The exact gold answers are decided in #63, not here.

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

## New knowledge needed (#58)

Eight policy versions across seven sources: `POL-UHC-MRI-LSPINE` v1, `POL-CIGNA-SLEEP-TESTING` v1, `POL-AETNA-MRI-KNEE` v1 and v2, `POL-MEDICAREB-VITD-TESTING` v1, `POL-UHC-AUTH-ADV-IMAGING` v1, `POL-AETNA-EM-MOD25` v1, and `POL-BCBSAZ-CT-SINUS` v1. The criteria and formulas in `EVIDENCE_SPECS.md` are proposals that the corpus owner may reword, as long as each case's facts still map to the same criteria.

Batch B needs no documents. Case `CLM-2026-710313-L1` must have **no** note in the corpus. The decoy therapy note belongs to `CLM-2026-719925`, a claim that isn't in any manifest.

## Open questions for review

1. **Labeling procedure.** The development guide says every held-out case needs two independent labelers (rule 5). #63 instead has one primary labeler with one reviewer per batch. The team should confirm which one applies before labeling starts.
2. **Corpus effort.** Batches A and C need about 60 notes and 8 policy versions. Silas, please confirm this is feasible, or name families to shrink.
3. **Safety expectations.** Neha, please confirm the batch B cases tagged `required-evidence` match the rule you are defining in #49, especially `H-CODING-DX` (the corrected diagnosis isn't stated in the case) and `H-WAIVER-CONFLICT`.
4. **Development cases.** The guide also asks for at least 20 more development cases. They aren't part of #52.

## Regenerating

The claims, manifest, and evidence specs all come from the case table in [`build_cases.py`](build_cases.py). To change a case, edit the table and run `python datasets/held_out/build_cases.py` from the repository root; don't edit the JSON by hand. The script sits under `datasets/`, outside the runtime code, and records case facts only, never gold labels.
