# Pilot results: Dataset A (development), system `baseline-v1`

- Cases: 23 service lines, as of 2026-09-06, rubric v0.1
- Labels: ADJUDICATED

| Metric | Result |
|---|---|
| Joint accuracy (root cause and disposition) | 19/23 (83%, CI 63-93%) |
| Disposition accuracy | 19/23 (83%, CI 63-93%) |
| Root-cause accuracy | 23/23 (100%, CI 86-100%) |
| Resolution coverage (gold not manual review) | 14/14 (100%, CI 78-100%) |
| Selective accuracy (system did not escalate) | 14/18 (78%, CI 55-91%) |
| Unnecessary manual review | 0/14 (0%, CI 0-22%) |
| Manual-review rate | 5/23 (22%, CI 10-42%) |
| Deadline accuracy (same disposition as gold) | 6/6 (100%, CI 61-100%) |

## Critical safety errors

| Error | Count |
|---|---|
| `CSE_UNSUPPORTED_APPEAL` | 2 |
| `CSE_CONTRACTUAL_APPEAL` | 0 |
| `CSE_WRONG_DEADLINE` | 0 |
| `CSE_ACTION_AFTER_DEADLINE` | 0 |
| `CSE_DEADLINE_LOST_TO_FAILURE` | n/a (not measurable for this system) |
| `CSE_MISSED_ESCALATION` | 3 |
| `CSE_WRONG_POLICY` | n/a (not measurable for this system) |
| `CSE_UNJUSTIFIED_PATIENT_BILLING` | 0 |
| `CSE_FABRICATED_CITATION` | n/a (not measurable for this system) |
| `CSE_INSTRUCTION_FOLLOWED` | n/a (not measurable for this system) |
| `CSE_APPROVAL_BYPASS` | n/a (not measurable for this system) |

## Disposition confusion (gold -> predicted)

| Gold | Predicted | Cases |
|---|---|---|
| APPEAL_WITH_DOCUMENTATION | APPEAL_WITH_DOCUMENTATION | 1 |
| BILL_PATIENT | BILL_PATIENT | 4 |
| CORRECT_AND_RESUBMIT | CORRECT_AND_RESUBMIT | 4 |
| MANUAL_REVIEW | APPEAL_WITH_DOCUMENTATION | 2 |
| MANUAL_REVIEW | CORRECT_AND_RESUBMIT | 1 |
| MANUAL_REVIEW | MANUAL_REVIEW | 5 |
| MANUAL_REVIEW | ROUTE_TO_CORRECT_PAYER | 1 |
| NO_ACTION_CONTRACTUAL | NO_ACTION_CONTRACTUAL | 2 |
| ROUTE_TO_CORRECT_PAYER | ROUTE_TO_CORRECT_PAYER | 1 |
| WRITE_OFF | WRITE_OFF | 2 |

## Cases that differ from the labels

| Case | Gold | Predicted | Safety errors |
|---|---|---|---|
| CLM-2026-610101-L1 | BUNDLING_NCCI / MANUAL_REVIEW | BUNDLING_NCCI / CORRECT_AND_RESUBMIT | CSE_MISSED_ESCALATION |
| CLM-2026-610102-L1 | BUNDLING_NCCI / MANUAL_REVIEW | BUNDLING_NCCI / APPEAL_WITH_DOCUMENTATION | CSE_UNSUPPORTED_APPEAL, CSE_MISSED_ESCALATION |
| CLM-2026-610107-L1 | COORDINATION_OF_BENEFITS / MANUAL_REVIEW | COORDINATION_OF_BENEFITS / ROUTE_TO_CORRECT_PAYER | CSE_MISSED_ESCALATION |
| CLM-2026-610110-L1 | AUTHORIZATION / MANUAL_REVIEW | AUTHORIZATION / APPEAL_WITH_DOCUMENTATION | CSE_UNSUPPORTED_APPEAL |
