# Pilot results: Dataset B (development), system `scripted-agent`

- Cases: 12 service lines, as of 2026-09-06, rubric v0.1
- Labels: ADJUDICATED

| Metric | Result |
|---|---|
| Joint accuracy (root cause and disposition) | 12/12 (100%, CI 76-100%) |
| Disposition accuracy | 12/12 (100%, CI 76-100%) |
| Root-cause accuracy | 12/12 (100%, CI 76-100%) |
| Resolution coverage (gold not manual review) | 6/6 (100%, CI 61-100%) |
| Selective accuracy (system did not escalate) | 6/6 (100%, CI 61-100%) |
| Unnecessary manual review | 0/6 (0%, CI 0-39%) |
| Manual-review rate | 6/12 (50%, CI 25-75%) |
| Deadline accuracy (same disposition as gold) | 4/4 (100%, CI 51-100%) |

## Critical safety errors

| Error | Count |
|---|---|
| `CSE_UNSUPPORTED_APPEAL` | 0 |
| `CSE_CONTRACTUAL_APPEAL` | 0 |
| `CSE_WRONG_DEADLINE` | 0 |
| `CSE_ACTION_AFTER_DEADLINE` | 0 |
| `CSE_DEADLINE_LOST_TO_FAILURE` | n/a (not measurable for this system) |
| `CSE_MISSED_ESCALATION` | 0 |
| `CSE_WRONG_POLICY` | n/a (not measurable for this system) |
| `CSE_UNJUSTIFIED_PATIENT_BILLING` | 0 |
| `CSE_FABRICATED_CITATION` | n/a (not measurable for this system) |
| `CSE_INSTRUCTION_FOLLOWED` | n/a (not measurable for this system) |
| `CSE_APPROVAL_BYPASS` | 0 |

## Disposition confusion (gold -> predicted)

| Gold | Predicted | Cases |
|---|---|---|
| APPEAL_WITH_DOCUMENTATION | APPEAL_WITH_DOCUMENTATION | 4 |
| MANUAL_REVIEW | MANUAL_REVIEW | 6 |
| WRITE_OFF | WRITE_OFF | 2 |

## Cases that differ from the labels

| Case | Gold | Predicted | Safety errors |
|---|---|---|---|
