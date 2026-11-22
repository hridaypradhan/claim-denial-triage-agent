# Final held-out evaluation (`final-heldout-v1`): evidence

This folder holds the evidence for the final project results and poster.

## What is preserved, and where

| Item | Location | SHA-256 |
|---|---|---|
| Full run bundle (all 1,993 files: predictions, output packages, redacted traces, post-decision records, failure records, abandoned partial folders, plus the 4 run and score logs) | GitHub release `final-heldout-v1`, asset `final-heldout-v1-bundle.zip` (1,904,502 bytes) | `543c26f596e5775824020e700e1c4460bc6cae862d54e339aa2ea358ab685c35` |
| Per-file checksums for the bundle | [`SHA256SUMS-files.txt`](SHA256SUMS-files.txt) (also inside the zip) | n/a |
| Experiment record | [`experiment.json`](experiment.json) | `8ce1a8e82d4fbd07c5a578d73caf2871bcac3f79d1959741c83383f67d82e006` |
| Scored results | [`results.json`](results.json) | `ffefdd8fe62b924adf94569a71a30e3539eec420f724c0ed57af9ea70bb69499` |
| Failure index (13 attempt records) | [`failure-index.json`](failure-index.json) | n/a |
| Derived values (script and its output) | [`derived_values.py`](derived_values.py), [`derived_values_output.txt`](derived_values_output.txt) | n/a |

**Frozen configuration:** these values are recorded in `experiment.json`.

| Item | Value |
|---|---|
| Code commit | `7b48ac307d3a8f9b70c54c9dbd76e682526ce624` |
| Plan | [`evaluation/final_experiment_plan.json`](../../final_experiment_plan.json) at that commit, sha256 `e0394310b9b39241234b910a18d74ebe59fb26fad777639af2b5c12b37fbea56` |
| Held-out dataset manifest | sha256 `a2ed818d8dfa7e58bc4cd96ddca8ea362afe0948cf5dadfead0f724b657bdb5e`, 80 cases, as of 2026-10-30 |
| Label lock (`datasets/labels/HELD_OUT_LABELS_LOCK.json`) | sha256 `2841dd963e2219f556ec3d991d4f879b52dd3c8bd01057ea1f5a12b04a430098` |
| Knowledge snapshot `knowledge-v2` | manifest sha256 `4a2f61a303bfdd14ee99be13ea4403c2e3b3849410e5455a3f0fda4b3e75681e`, lock sha256 `20cf6b0b25c0d4cb103a02bb25cd9253f135487ffc5307f084b1aeceaf8a3ca5` |

The API key appears nowhere in the bundle. Every file was scanned for it before the zip was built.

## Reproducing

From a clean checkout of the frozen commit, with the plan above and your own `CLAIM_AGENT_LLM_API_KEY`:

```
python -m evaluation.final_experiment run --plan evaluation/final_experiment_plan.json --code-commit 7b48ac307d3a8f9b70c54c9dbd76e682526ce624 --confirm-live
python -m evaluation.final_experiment score --plan evaluation/final_experiment_plan.json
```

To re-score without any model calls, unzip the bundle to `.local-output/final-experiment/` and run only the `score` command.

To recompute the derived values:

```
python evaluation/results/final-heldout-v1/derived_values.py .local-output/final-experiment/final-heldout-v1 .
```

## Where each reported value comes from

**Notation:**
- `B` is `results.json → systems.baseline-v1.repeats.repeat-1`.
- `A[n]` is `results.json → systems.hosted-agent.repeats.repeat-n`.
- `D` is `derived_values_output.txt`.

### Main results table

| Value | Source |
|---|---|
| Joint accuracy 42/80 (CI 41.7–63.1) vs 60/80 (CI 64.5–83.2) | `B.joint_accuracy`, `A[1..3].joint_accuracy` (each has `hits`, `total`, `wilson_95`) |
| Root cause 80/80; deadline 20/20 vs 17/17 | `*.root_cause_accuracy`, `*.deadline_accuracy` |
| Selective accuracy 30/65 vs 27/28, 27/29, 27/29 | `*.selective_accuracy` |
| Coverage 30/46 vs 27/46 | `*.coverage` |
| Manual-review rate 15/80 vs 52, 51, 51 | `*.manual_review_rate` |
| Unnecessary manual review 3/46 vs 19/46, 18/46, 18/46 | `*.unnecessary_manual_review` |
| Critical safety errors 47 (30 unsupported appeals, 17 missed escalations) vs 4/2/2 | `B.critical_safety_errors`, `A[n].critical_safety_errors`; totals and case counts in `D` |
| Stability 77/80 | `systems.hosted-agent.stability.cases_with_identical_answers_across_repeats` |

### Agent safety errors

| Value | Source |
|---|---|
| Fabricated citations, all on `CLM-2026-710308-L1` | `A[n].trace_measures.unretrieved_citations` |
| Those decisions were rejected, so 0 reached a final package | `A[n].post_decision.decision_stage.critical_safety_errors` |
| `CLM-2026-710220-L1` (missed escalation) is on the deterministic route | `D` (route of 710220), from `runs/hosted-agent/CLM-2026-710220-L1/repeat-*/output-package.json` |

### Verifier and drafter

| Value | Source |
|---|---|
| Decisions proposed and accepted | `A[n].trace_measures.decisions_proposed`, `.decisions_accepted` |
| Verifier outcomes, escalations, drafts, provider failures | `A[n].post_decision.*` |
| All 9 verifier escalations were `NO_ACTION_CONTRACTUAL` where gold is `WRITE_OFF` | `D`, from `runs/hosted-agent/*/repeat-*/post-decision.json` and the locked labels |
| All 33 accepted drafts are on gold-`APPEAL_WITH_DOCUMENTATION` cases | `D` |

### Other measures

| Value | Source |
|---|---|
| Citation precision 98.0% / 99.3% / 99.3%, pooled 435/440 = 98.9% | `A[n].trace_measures.citation_precision`; pooled in `D` |
| Policy requests matching the claim 41/41, 41/41, 42/42 | `A[n].trace_measures.policy_requests_matching_claim` |
| Budget overruns 0; unterminated runs 0 | `A[n].trace_measures.budget_overruns`, `.unterminated_runs` |
| Latency of the 240 completed and scored agent runs: median 23.1 s, p95 120.4 s (wall clock, whole pipeline) | `systems.hosted-agent.operations.wall_clock_ms_median`, `.wall_clock_ms_p95` |
| Provider usage of the 240 completed and scored runs: **490 calls, 977,566 input and 218,488 output tokens**. That is the decision stage (413 calls, 682,377 / 143,226) plus the verifier and drafter (77 calls, 295,189 / 75,262) | `systems.hosted-agent.operations` (top level = decision stage) + `operations.post_decision`; summed in `D` |
| Provider usage of the 13 failed attempts (not scored, not in the row above): **47 calls, 95,102 input and 21,386 output tokens**. That is the decision stage (40 calls, 66,331 / 14,240) plus post-decision (7 calls, 28,771 / 7,146); usage was reported for every call | `D`, from `runs/hosted-agent/*/*.abandoned-*/measurements.json` (top level + `post_decision`, checked against `pipeline`) |
| Total provider usage including failed attempts: **537 calls, 1,072,668 input and 239,874 output tokens** | `D` (sum of the two rows above) |

### Poster

| Value | Source |
|---|---|
| 60/80 under the 2-of-3 rule (all three runs correct: 59; at least one run correct: 61) | `D` |
| Manual review for every case 34/80 | gold-label counts in `D` |
| 96% (27/28) | `A[1].selective_accuracy` |
| Rules-only 46% | `B.selective_accuracy` |

## Run failures

12 runs first failed with a Windows `PermissionError` when the finished run folder was renamed into place. The repo sat in a OneDrive folder that likely held a lock. The harness resume reran only those runs on the same commit, with no changes:
- first resume: 11 completed and 1 failed again;
- second resume: that run completed.

All 13 attempt records are listed in `failure-index.json` and `results.json → failed_attempts_recorded`. The half-written folders are kept in the bundle as `*.abandoned-*` and are not scored. Their model calls had already completed, so their provider usage is reported separately above; one of the 13 (`CLM-2026-710211-L1`, repeat 2) was a deterministic run with no model calls. No code, prompt, plan or label was changed after held-out execution began.
