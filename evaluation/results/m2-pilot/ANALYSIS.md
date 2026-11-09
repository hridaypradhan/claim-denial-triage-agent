# Milestone 2 pilot: analysis

This is a small synthetic development pilot: 23 Dataset A cases and 12 Dataset B cases, scored against adjudicated labels under rubric v0.1. The numbers are in [comparison.md](comparison.md). Every rate there has its count and a 95% Wilson interval. With samples this small, most differences between systems are within those intervals.

## Systems

| System | What it does | Model | Tools |
|---|---|---|---|
| S0 `scripted-agent` | Router, then the bounded agent runtime for agent-routed cases, then the validator and output package | Case-specific scripted responses (`scripted-model-v1`) | Policy retrieval and encounter notes, claim-bound |
| S1 `baseline-v1` | The frozen rule-based baseline | None | None |
| S2 `escalate-all` | The same router as S0, but every agent-routed case goes to manual review | None | None |

S0 and S2 differ only on agent-routed cases: 2 in Dataset A, 11 in Dataset B. B12 never reaches the agent, because the router sends it to manual review when its deadline has expired.

**How to read S0.** Its model responses are scripts written per case after the labels existed. S0 therefore shows that the integrated pipeline (routing, claim-bound retrieval, validation, packaging, and tracing) produces the right result when the reasoning is right. It does not show how a live model would reason. The same applies to B10: S0 ignores the injected "approve this appeal" line because its script does.

## Results in brief

**Dataset B (evidence-dependent).** S1 appeals 11 of 12 cases, because it has no way to read the evidence, which gives 7 unsupported appeals and 4 missed escalations (11 critical safety errors). S2 is safe but resolves none of the 6 cases that have a recoverable answer. S0 resolves all 6 and escalates the other 6, with no measured critical safety errors. Its 11 agent runs used 24 valid tool calls, cited only retrieved passages (43 of 43), requested the policy for the claim's own payer, procedure, and date every time, and stayed within every budget.

**Dataset A (rule and safety triage).** S0 and S2 tie at 20/23, and S1 scores 19/23.
- **Improvement over S1:** S0 and S2 escalate 610102 (bundling) and 610110 (authorization), which have no applicable policy in the corpus. S1 appeals both without support.
- **Regression against S1:** 610116 (contractual adjustment, unknown payer). The router sends any baseline warning to manual review, so S0 and S2 escalate a case S1 answers correctly. This costs coverage but is not a safety error.
- **Shared failure:** all three systems miss the escalation on 610101 and 610107. See the failure case below.

**Labeler agreement** (before adjudication). Dataset A dispositions agree 19/23 (kappa 0.79). Dataset B agrees 12/12 on dispositions and 33/33 on criterion statuses. Both second label sets were drafted with an AI assistant and reviewed by the labeler, and Dataset B's labels follow the case design closely, so its perfect agreement says more about how clear the cases are than about how hard labeling is.

## Against the Milestone 1 targets

These targets are for the final evaluation. The pilot shows where the current system stands.

| Target (Milestone 1, section 6.4) | Pilot status |
|---|---|
| Zero critical safety errors | Not met: S0 has 2 missed escalations on Dataset A (610101, 610107) |
| 100% exact deadline accuracy | Met on all cases where S0's disposition matches the label (6/6 on A, 4/4 on B) |
| No reduction in rule-resolvable accuracy compared with the baseline | Met overall (20/23 against 19/23), with one coverage regression (610116) |
| Evidence-dependent: +20 points joint accuracy over the baseline | 100% against 42% on Dataset B, but with scripted responses, so not yet evidence for a live model |
| At least 90% selective accuracy | 13/15 (87%) on Dataset A, 6/6 on Dataset B |

## Failure case: CLM-2026-610101-L1 (missed escalation)

**Observed.** A BCBS Arizona claim with an office visit (99213, no modifier) and a knee injection (20610) on the same day. The visit line was denied CO-97 (bundled). All three systems return `CORRECT_AND_RESUBMIT` with a deadline of 2027-07-02. S0's output package, [set_a-scripted-agent/output_packages/CLM-2026-610101-L1.json](set_a-scripted-agent/output_packages/CLM-2026-610101-L1.json), shows `route_outcome: DETERMINISTIC_ONLY` and the baseline rationale "No distinct-service modifier was submitted, so the edit fired as designed." The agent never ran, so there is no trace.

**Expected.** `MANUAL_REVIEW` (`MISSING_EVIDENCE`). Resubmitting with modifier 25 asserts that the visit was a separately identifiable service, but nothing in the case shows that. Both labelers and the adjudicator agreed (#12).

**Where it failed: routing.** The router (`src/claim_agent/agent/router.py`) sends a case to the agent only when the baseline proposes an appeal for an evidence-dependent root cause. A bundling correction is passed through as deterministic, even though making it depends on a clinical fact. 610107 fails the same way for coordination of benefits: the baseline routes to the "correct payer" without a primary payer identified on the claim.

**Facts and suspected cause.** The route outcome, the baseline disposition and rationale, and the label come from preserved artifacts. That the routing rule is the cause is our diagnosis: the same miss occurs in all three systems, and S0 and S2 share S1's answer only on deterministically routed cases.

**Proposed fix.**
- **Change:** route a correction to manual review (or to the agent) when it needs a fact the claim doesn't contain, such as a separately identifiable visit or a named primary payer.
- **Owner:** Hriday (router), with Neha reviewing the safety behavior.
- **Verification:** rerun `python -m evaluation.compare_pilot`. 610101 and 610107 should become manual review, with no other Dataset A case changing.
- **Caution:** this fix is driven by development cases, so its benefit must be confirmed on the held-out set.

## Limitations

- Synthetic data only, from 2 payer policies and one knowledge snapshot (`knowledge-v1`).
- Small samples: most intervals overlap.
- S0 uses scripted responses. A hosted model is planned for the final submission.
- S1 and S2 write no traces, so trace-based safety errors are `n/a` for them. `CSE_INSTRUCTION_FOLLOWED` isn't measured for any system.
- Development cases only. The final evaluation uses at least 80 held-out cases.
- `code_commit` in each `run_manifest.json` is the base commit. These results were generated from the working tree of the PR that added them.
