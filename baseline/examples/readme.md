# Configuration and input format

Everything the baseline reads lives in two places: reference data in [`data/`](../data), and claim records in this folder. There is no application config file, no environment variable, and no API key.

## Reference data (`data/`)

| File | What it holds | When you edit it |
| --- | --- | --- |
| `denial_codes.json` | CARC and RARC descriptions, plus the root-cause category each CARC maps to | Adding a denial code the payer mix uses |
| `payer_rules.json` | Per-payer appeal window, corrected-claim window, retro-auth window, appeal name, submission channel | Onboarding a payer or updating a contract |

`payer_rules.json` has a `default` block that is merged under every payer entry, so a new payer only needs the fields that differ. A payer that is absent entirely still triages — the baseline falls back to the defaults and raises a warning on the report.

The filing windows shipped here are illustrative defaults so the deadline math is demonstrable. Real windows are contract-specific and come from the practice's payer matrix.

## Claim record schema

One JSON object per denied claim.

| Field | Required | Notes |
| --- | --- | --- |
| `claim_id` | yes | Used to name the output files |
| `payer` | yes | Key into `payer_rules.json`, case-insensitive |
| `date_of_service` | yes | `YYYY-MM-DD`; corrected-claim deadlines count from here |
| `remit_date` | yes | `YYYY-MM-DD`; appeal deadlines count from here |
| `denials` | yes | At least one entry; see below |
| `patient_account` | no | Printed on the report and the appeal letter |
| `billed_amount`, `paid_amount` | no | Default `0.0` |
| `service_lines` | no | Joined to denials on `line` |
| `encounter` | no | The facts the rules branch on; see below |

### `denials[]`

| Field | Default | Notes |
| --- | --- | --- |
| `carc` | required | Claim Adjustment Reason Code, without the group prefix |
| `group_code` | `CO` | `CO`, `PR`, `OA`, or `PI`. A `PR` code moves the balance to the patient |
| `rarc` | `[]` | Remark codes. These are what turn a vague CO-16 into an actionable fix |
| `amount` | `0.0` | Dollars denied on this line |
| `service_line` | `1` | Joins to `service_lines[].line` |

### `service_lines[]`

`line`, `cpt`, `modifiers`, `units`, `charge`, `icd10`.

### `encounter`

Optional facts. Absent means "unknown", which generally pushes the claim toward `MANUAL_REVIEW` rather than a guess.

| Field | Effect |
| --- | --- |
| `prior_auth_number` | An auth denial becomes a resubmission instead of an appeal |
| `documentation_attached` | Clinical documents become appeal enclosures; their absence adds them to `evidence_needed` |
| `proof_of_timely_filing` | Makes a CO-29 appealable instead of a write-off |
| `abn_signed` | Lets a non-covered charge move to the patient instead of a write-off |
| `primary_eob_on_file` | Turns a COB denial into a resubmission |
| `alternate_coverage` | Routes an eligibility denial to the other payer |
| `referral_on_file`, `eligibility_verified_on` | Carried for audit; not yet branched on |

## Example set

| File | Denial | Expected outcome |
| --- | --- | --- |
| `test1_auth_missing.json` | CO-197, auth on file | `CORRECT_AND_RESUBMIT` |
| `test2_medical_necessity.json` | CO-50 + M127 | `APPEAL_WITH_DOCUMENTATION` + draft letter |
| `test3_coding_incomplete.json` | CO-16 + N290 | `CORRECT_AND_RESUBMIT` |
| `test4_contractual_and_patient.json` | CO-45 and PR-204 | `NO_ACTION_CONTRACTUAL` and `BILL_PATIENT` |
| `test5_timely_filing.json` | CO-29, no proof | `WRITE_OFF` |
| `test6_ambiguous.json` | Bare CO-16, unknown payer | `MANUAL_REVIEW`, two warnings |

`labels.json` carries the ground truth for this set and is what the test suite scores against. It is skipped by the batch runner.

All claim data here is synthetic. No real patient, provider, or payer records are used anywhere in this repository.
