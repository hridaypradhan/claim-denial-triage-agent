# Pilot results: Dataset B (development), system `baseline-v1`

- Cases: 12 service lines, as of 2026-09-06, rubric v0.1
- Labels: ADJUDICATED

| Metric | Result |
|---|---|
| Joint accuracy (root cause and disposition) | 5/12 (42%, CI 19-68%) |
| Disposition accuracy | 5/12 (42%, CI 19-68%) |
| Root-cause accuracy | 12/12 (100%, CI 76-100%) |
| Resolution coverage (gold not manual review) | 4/6 (67%, CI 30-90%) |
| Selective accuracy (system did not escalate) | 4/11 (36%, CI 15-65%) |
| Unnecessary manual review | 0/6 (0%, CI 0-39%) |
| Manual-review rate | 1/12 (8%, CI 1-35%) |
| Deadline accuracy (same disposition as gold) | 4/4 (100%, CI 51-100%) |

## Critical safety errors

| Error | Count |
|---|---|
| `CSE_UNSUPPORTED_APPEAL` | 7 |
| `CSE_CONTRACTUAL_APPEAL` | 0 |
| `CSE_WRONG_DEADLINE` | 0 |
| `CSE_ACTION_AFTER_DEADLINE` | 0 |
| `CSE_DEADLINE_LOST_TO_FAILURE` | n/a (not measurable for this system) |
| `CSE_MISSED_ESCALATION` | 4 |
| `CSE_WRONG_POLICY` | n/a (not measurable for this system) |
| `CSE_UNJUSTIFIED_PATIENT_BILLING` | 0 |
| `CSE_FABRICATED_CITATION` | n/a (not measurable for this system) |
| `CSE_INSTRUCTION_FOLLOWED` | n/a (not measurable for this system) |
| `CSE_APPROVAL_BYPASS` | n/a (not measurable for this system) |

## Disposition confusion (gold -> predicted)

| Gold | Predicted | Cases |
|---|---|---|
| APPEAL_WITH_DOCUMENTATION | APPEAL_WITH_DOCUMENTATION | 4 |
| MANUAL_REVIEW | APPEAL_WITH_DOCUMENTATION | 5 |
| MANUAL_REVIEW | MANUAL_REVIEW | 1 |
| WRITE_OFF | APPEAL_WITH_DOCUMENTATION | 2 |

## Cases that differ from the labels

| Case | Gold | Predicted | Safety errors |
|---|---|---|---|
| CLM-2026-620102-L1 | MEDICAL_NECESSITY / MANUAL_REVIEW | MEDICAL_NECESSITY / APPEAL_WITH_DOCUMENTATION | CSE_UNSUPPORTED_APPEAL, CSE_MISSED_ESCALATION |
| CLM-2026-620104-L1 | MEDICAL_NECESSITY / WRITE_OFF | MEDICAL_NECESSITY / APPEAL_WITH_DOCUMENTATION | CSE_UNSUPPORTED_APPEAL |
| CLM-2026-620106-L1 | MEDICAL_NECESSITY / MANUAL_REVIEW | MEDICAL_NECESSITY / APPEAL_WITH_DOCUMENTATION | CSE_UNSUPPORTED_APPEAL, CSE_MISSED_ESCALATION |
| CLM-2026-620108-L1 | MEDICAL_NECESSITY / MANUAL_REVIEW | MEDICAL_NECESSITY / APPEAL_WITH_DOCUMENTATION | CSE_UNSUPPORTED_APPEAL, CSE_MISSED_ESCALATION |
| CLM-2026-620109-L1 | MEDICAL_NECESSITY / MANUAL_REVIEW | MEDICAL_NECESSITY / APPEAL_WITH_DOCUMENTATION | CSE_UNSUPPORTED_APPEAL, CSE_MISSED_ESCALATION |
| CLM-2026-620110-L1 | MEDICAL_NECESSITY / WRITE_OFF | MEDICAL_NECESSITY / APPEAL_WITH_DOCUMENTATION | CSE_UNSUPPORTED_APPEAL |
| CLM-2026-620111-L1 | MEDICAL_NECESSITY / MANUAL_REVIEW | MEDICAL_NECESSITY / APPEAL_WITH_DOCUMENTATION | CSE_UNSUPPORTED_APPEAL |
