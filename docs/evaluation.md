# Evaluation

The project has two separate evaluation stages. The development pilot verified
integration with scripted responses. The frozen held-out experiment measured
the selected hosted model. Results from the two stages must not be mixed.

## How the result files differ

The Milestone 2 folder is a preserved development snapshot, so its filenames
still describe that milestone:

- `comparison.json` is the machine-readable output used by code. It contains
  exact counts, rates, confidence intervals, and per-system values.
- `comparison.md` presents the same comparison as tables for a person to read.
- `ANALYSIS.md` interprets those pilot tables, explains their limits, and
  discusses the development targets.

Those files are not the final project analysis. Final hosted results live under
`evaluation/results/final-heldout-v1/`: `results.json` is machine-readable,
while `EVIDENCE.md` explains the numbers, hashes, limitations, and derivations.
The older filenames are retained because the result folder is historical
evidence rather than current project documentation.

## Development pilot

The Milestone 2 pilot compared the integrated scripted agent, the unchanged
`baseline-v1`, and an `escalate-all` control on small synthetic development
sets. It tested contracts, routing, retrieval, validation, packaging, and
metrics—not language-model generalization. Its preserved report is
[`evaluation/results/m2-pilot/comparison.md`](../evaluation/results/m2-pilot/comparison.md).

Rerun it without network access:

```bash
python -m evaluation.compare_pilot --outdir .local-output/eval/m2-pilot-rerun
```

## Hosted-model development validation

Candidate models were tested only on development cases. Compatibility gates
checked structured actions, authorized tool use, evidence completeness,
grounding, provider reliability, and critical safety errors. The accepted
configuration uses `beam-1-0` through ASU Voyager. Selection history is
preserved in [ADR 0003](decisions/0003-hosted-model-provider-and-selection.md).

The final development gate used eight cases with three repetitions. Beam
completed 24 runs with zero provider failures, zero critical safety errors,
and 75% joint accuracy. This selected a compatible bounded model; it did not
establish production readiness.

Validate the plan offline:

```bash
python -m evaluation.hosted_comparison
```

## Frozen held-out experiment

The final experiment used 80 held-out synthetic cases, frozen reviewed labels,
knowledge snapshot `knowledge-v2`, `baseline-v1` once per case, and the hosted
agent three times per case. The full planning, deterministic validation,
tool-less verification, and grounded drafting pipeline was enabled.

The evaluated code commit was
`7b48ac307d3a8f9b70c54c9dbd76e682526ce624`. Model, prompt, limits, datasets,
labels, knowledge, and scoring definitions were frozen before the run. Held-out
failures were preserved and did not trigger tuning.

| Measure | Rules only | Hosted agent |
|---|---:|---:|
| Joint accuracy | 42/80 | 60/80 in each repeat |
| Selective accuracy | 30/65 | 27/28, 27/29, 27/29 |
| Critical safety errors | 47 | 4, 2, 2 |
| Manual-review cases | 15/80 | 52/80, 51/80, 51/80 |
| Citation precision | not measured | 145/148, 146/147, 144/145 |
| Cross-repeat stability | n/a | 77/80 identical |

The agent was more accurate and much safer on this test set, but also more
conservative. The high manual-review rate is an important limitation.

## Preserved evidence

The authoritative
[final evidence record](../evaluation/results/final-heldout-v1/EVIDENCE.md)
contains the evaluated commit, frozen hashes, release checksum, exact metric
derivations, provider usage, latency, verifier and drafter outcomes, and failed-
attempt index. The full 1,993-file bundle is attached to the GitHub release
tagged `final-heldout-v1`.

## Reproduce or re-score

Check the plan without provider calls:

```bash
python -m evaluation.final_experiment check --plan evaluation/final_experiment_plan.json
```

Run live only from the frozen commit with an individual Voyager key:

```bash
python -m evaluation.final_experiment run \
  --plan evaluation/final_experiment_plan.json \
  --code-commit 7b48ac307d3a8f9b70c54c9dbd76e682526ce624 \
  --confirm-live
```

Score a complete local bundle without model calls:

```bash
python -m evaluation.final_experiment score --plan evaluation/final_experiment_plan.json
```

To re-score the release, unpack it under
`.local-output/final-experiment/final-heldout-v1/`. The scorer rechecks frozen
hashes and refuses incomplete or mismatched inputs.

Recompute the additional published values:

```bash
python evaluation/results/final-heldout-v1/derived_values.py \
  .local-output/final-experiment/final-heldout-v1 .
```

On Windows PowerShell, use one line or replace each Bash continuation (`\`) with
a PowerShell backtick.

## Interpretation limits

- All records and policies are synthetic.
- Eighty cases are not a representative production sample.
- Confidence intervals describe this test set only.
- Provider latency and availability can change.
- Every package requires human review.
- Later documentation commits were not part of the frozen experiment.
