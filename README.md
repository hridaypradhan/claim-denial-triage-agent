# Claim Denial Triage Agent

A hybrid agentic AI system for evidence-grounded triage of denied outpatient medical claims. The project combines a deterministic Python core with bounded LLM tool use, evidence retrieval, safety validation, and human review.

> **Project status:** Active academic capstone. The deterministic baseline,
> shared contracts, local retrieval tools, safety validator, bounded runtime,
> a provider-neutral hosted-model boundary, and a production-shaped local
> integration and durable canonical traces are runnable and tested. A
> provider-specific hosted transport, evaluation, and the final report remain
> in progress.

## Problem

Outpatient practices lose recoverable revenue when denied claims are triaged late or incorrectly. A reviewer must determine why a claim was denied, what action is appropriate, what evidence is missing, and whether a filing deadline is still open.

This project is designed to produce a review package for an accounts-receivable specialist. It does **not** submit claims or appeals, connect to a live payer or EHR, or make an autonomous final decision.

## Current and target architecture

The solid path below is implemented in the local integrated demonstration.
Dashed nodes are tracked final-phase extensions; they are not required to
describe the current Milestone 2 runtime as implemented.

```mermaid
flowchart TD
    A[Denied claim] --> B[Validate input]
    B --> C[Deterministic baseline rules]
    C --> D{Rule-decidable?}
    D -- Yes --> S[Deterministic structural checks]
    D -- "No: policy missing or inapplicable" --> J[Manual review]
    D -- "No: evidence-dependent" --> E[Initialize case state]

    subgraph Agent["Bounded LLM agent: at most 6 planning turns and 10 tool calls"]
        E --> P[Plan next step]
        P --> T[Call tool]
        T --> U[Update criteria assessments]
        U -- Missing evidence --> P
        U -- Supported decision --> R[Decision or MANUAL_REVIEW]
        U -- Budget exhausted --> R
    end

    T <--> F[Common dispatcher for read-only tools]
    F --> G[(Policy and encounter evidence)]
    F -. planned .-> I[(Eligibility evidence)]

    R --> S
    S -- Rule-only finding --> O{Outcome?}
    S -- Evidence-dependent finding --> V[Semantic check: tool-less LLM verifier]
    V --> O
    O -- Not appealable --> K[Report only]
    O -- Critical failure --> J
    O -- Eligible appeal --> W[Draft appeal from verified evidence]
    W --> X{Draft supported?}
    X -- Yes --> L[Report: PENDING_REVIEW]
    X -- No --> J
    K --> L
    J --> L
    L -.-> Q[(Audit trace saved separately)]
    L --> H[Human review: edit, approve, or reject]
    H --> Z[Save outputs]

    M[Provider-specific hosted transport] -. planned .-> P

    classDef planned stroke-dasharray: 5 5,fill:#fff;
    class I,M,V,W,X planned;
```

The model will be used only where evidence interpretation is necessary. Exact and safety-critical behavior—including input validation, deadline arithmetic, permitted dispositions, citation checks, and final approval state—remains under deterministic control.

## What is implemented

### Runnable deterministic baseline

The frozen stakeholder baseline currently provides:

- Validation of synthetic professional outpatient claim JSON.
- Mapping for 16 Claim Adjustment Reason Codes (CARCs) and 9 Remittance Advice Remark Codes (RARCs).
- Ten root-cause categories plus explicit unknown/manual-review handling.
- Payer-profile lookup with five illustrative payer profiles and a default fallback.
- Rule-based disposition recommendations and action plans.
- Deterministic filing-deadline calculation.
- Human-readable text reports and structured JSON output.
- Draft appeal letters for findings the baseline classifies as appealable.
- Six labeled synthetic example claims.
- Nine passing unit and regression tests.

The unchanged comparison baseline is preserved under [`baseline/`](baseline/) and tagged as `baseline-v1`.

### Knowledge corpus v0

The [`knowledge/`](knowledge/) directory contains the local evidence snapshot
used by the integrated runtime demonstration. Per-source SHA-256 hashes pin the
exact bytes used by a run and make later corpus changes detectable.

It contains:

- A [manifest](knowledge/manifest.json) listing every source with its version, `sha256` hash, payer, procedure codes or claim ID, effective dates, and origin.
- A synthetic medical policy for brain MRI (CPT 70551–70553) under the `BCBS_AZ` payer profile, with a criteria formula for each procedure code (`A AND B` for 70551 and 70552; `A AND B AND (C OR D)` for 70553). It is not the payer's actual policy.
- A synthetic physical therapy policy (CPT 97110) under the `AETNA` payer profile in two dated versions, so the service date selects the criteria: v1 (`A AND B`) through 2026-03-31 and v2 (`A AND B AND C`) from 2026-04-01. It is not the payer's actual policy.
- Two synthetic encounter documents for the example MRI claim `CLM-2026-005097`: a clinical note that documents criteria A and B but is silent on C and D, and a neurology progress note that documents C.

Every passage has a stable citation ID such as
`POL-BCBSAZ-MRI-BRAIN@v1#P3`. The retrieval method and ID rules are recorded in
[decision 0001](docs/decisions/0001-retrieval-method.md).
`retrieve_policy_passages` matches one policy by payer, procedure, and service
date, then ranks its passages with BM25. `ToolDispatcher` exposes it as
`retrieve_medical_policy` and also provides `get_encounter_note`, which returns
a claim's note as numbered, citable passages. It also provides
`lookup_denial_code` and `get_payer_profile`, which read the frozen baseline
tables. All four return the shared `ToolResult` contract. Policy and encounter
retrieval are exercised by the integrated runtime; the new lookup tools remain
outside the agent's route allowlist pending routing-owner approval. An unknown
CARC or unlisted payer returns `NO_MATCH`, not the baseline's default profile.
`calculate_deadline` and eligibility retrieval are not implemented yet.

### Scripted offline agent runtime

The agent package now provides an offline vertical slice for developing and verifying runtime behavior before production integrations are available:

- A provider-independent model interface and deterministic scripted model.
- Strict parsing of permitted structured model actions.
- Explicit runtime phases and controlled state transitions.
- Model-call, tool-call, repair, total-step, and elapsed-time budgets.
- Repeated planning after allowlisted read-only tool observations.
- Budget-limited correction of malformed or validation-rejected output; the
  demonstration permits one attempt.
- Deterministic decision validation through a temporary scripted boundary.
- Append-only in-memory trace entries and explicit terminal reasons.
- Conservative `MANUAL_REVIEW` results when the runtime cannot continue safely.
- A command-line demonstration and an end-to-end integration test using only synthetic data.

This vertical slice establishes runtime control flow; it is not a production
claim-review system. Scripted boundaries remain for deterministic regression
tests, while adapters connect the integrated path to the approved shared tool,
routing, decision, validation, output, and durable trace contracts. Internal
events remain an implementation detail; the canonical JSON Lines trace is the
stable evaluation boundary.

### Production-shaped local integration

The integrated local runner now exercises one authorized synthetic claim
through the merged project boundaries:

- Typed `BaselineFinding` and `RouteDecision` inputs are checked before any
  model or tool call.
- The route-derived tool allowlist controls real local policy and encounter
  retrieval.
- A claim-bound dispatcher guard requires policy payer, procedure, and service
  date—and encounter claim ID—to match the active claim before retrieval.
- Retrieved passages are stored per run and passed to deterministic safety
  validation.
- Policy-criterion formulas use a purpose-built Boolean parser rather than
  executable expression evaluation; malformed formulas fail closed.
- The validated shared `AgentDecision` and `ValidationResult` are preserved as
  audit artifacts.
- Structured validation problems retain their stable code, field path,
  message, and repairability flag when included in the bounded repair prompt.
- Every run produces an `OutputPackage`; even accepted recommendations remain
  `PENDING_REVIEW` and are never submitted automatically.

The model is still scripted, so this flow is reproducible and requires no
hosted-model credentials.

The runtime also provides a provider-neutral `HostedModelClient`. It translates
the stable runtime request into a timeout-bounded transport request and maps
provider text, request IDs, and token counts back into the runtime response.
Its fake-transport tests run offline. No live provider transport or credential
configuration is committed yet. Hosted-call timeouts consume the attempted
model-call budget, emit `MODEL_FAILED`, and produce an audited `MANUAL_REVIEW`
package with terminal reason `model_timeout`.

### Implemented packages and remaining boundaries

The repository is organized around the following implemented packages and
explicitly tracked extension points:

- [`src/claim_agent/contracts/`](src/claim_agent/contracts/) — shared data contracts.
- [`src/claim_agent/tools/`](src/claim_agent/tools/) — typed, read-only evidence tools.
- [`src/claim_agent/agent/`](src/claim_agent/agent/) — bounded runtime,
  production-shaped adapters, canonical traces, and the provider-neutral hosted
  boundary; provider-specific transport and grounded drafting remain in #34.
- [`src/claim_agent/safety/`](src/claim_agent/safety/) — deterministic validation and approval gates.
- [`src/claim_agent/output/`](src/claim_agent/output/) — review reports and appeal preparation.
- [`knowledge/`](knowledge/) — versioned policies and synthetic evidence (corpus v0 above; eligibility records not yet added).
- [`datasets/`](datasets/) — development, held-out, and labeling artifacts being
  completed through #12 and PR #32. Labels follow the frozen
  [annotation rubric](datasets/ANNOTATION_RUBRIC.md) (v0.1).
- [`evaluation/`](evaluation/) — accepted trace examples plus the comparison
  harness being completed through #12 and PR #32.

Eligibility retrieval, an agent-callable deadline wrapper, hosted-model
transport, evidence verification, grounded drafting, and the full comparative
evaluation remain explicitly tracked rather than implied by directory names.

## Safety boundaries

- Only synthetic data is used. No protected health information is included.
- The system will not submit a claim, appeal, or patient bill.
- Every report begins in a human-review state.
- Missing, conflicting, unsupported, or unavailable evidence must lead to manual review.
- Retrieved policy and clinical text is treated as untrusted data, not as instructions.
- Illustrative payer rules must not be interpreted as verified contract terms.

## Requirements

The baseline itself has no third-party dependencies. The agent, retrieval,
validation, and output packages use the dependencies pinned in
[`requirements.txt`](requirements.txt), including Pydantic.

The local demonstrations require:

- Python 3.9 or newer.
- No model API key.
- No network connection.

Future production integrations may introduce separate dependencies. Those dependencies will not be added to the frozen baseline.

## Setup

Clone the repository and enter its root directory:

```bash
git clone <repository-url>
cd claim-denial-triage-agent
```

Confirm Python is available:

```bash
python --version
```

On Windows, the Python launcher may be used instead:

```powershell
py -3 --version
```

No installation step is required to run the baseline.

## Run the baseline tests

From the repository root:

```bash
python -m unittest discover -s baseline/tests -p "test*.py" -v
```

On Windows, replace `python` with `py -3` if needed:

```powershell
py -3 -m unittest discover -s '.\baseline\tests' -p 'test*.py' -v
```

Expected result:

```text
Ran 9 tests
OK
```

## Run the offline agent tests

The agent runtime uses a `src` layout. From the repository root, expose that directory to Python and run the component and integration suites:

```bash
PYTHONPATH=src python -m unittest discover -s tests/agent -p "test*.py" -q
PYTHONPATH=src python -m unittest discover -s tests/integration -p "test*.py" -q
```

In PowerShell:

```powershell
$env:PYTHONPATH = '.\src'
python -m unittest discover -s '.\tests\agent' -p 'test*.py' -q
python -m unittest discover -s '.\tests\integration' -p 'test*.py' -q
```

The agent tests retain scripted boundaries for deterministic regression
coverage. The integration suite also exercises the real local dispatcher,
same-run evidence store, safety validator, and output package. Neither suite
requires network access or model credentials.

## Run the offline agent demonstration

The demonstration runs a synthetic medical-necessity denial through policy retrieval, evidence-context updating, decision proposal, deterministic validation, and trace emission:

```bash
PYTHONPATH=src python -m claim_agent.agent.offline_runner --pretty
```

In PowerShell:

```powershell
$env:PYTHONPATH = '.\src'
python -m claim_agent.agent.offline_runner --pretty
```

The JSON output reports the terminal phase, reason, accepted decision, consumed budgets, and ordered trace-event names. All model, tool, and validation responses in this demonstration are predefined fixtures rather than production services.

## Run the integrated local demonstration

This command keeps the deterministic scripted model but uses the shared
routing contracts, production-shaped local dispatcher, same-run evidence
store, safety validator, and audited output package:

```bash
PYTHONPATH=src python -m claim_agent.agent.integrated_runner --pretty
```

In PowerShell:

```powershell
$env:PYTHONPATH = '.\src'
python -m claim_agent.agent.integrated_runner --pretty
```

The JSON includes retrieved passage IDs, the selected policy formula, ordered
trace events, budget usage, and an `OutputPackage` whose approval state remains
`PENDING_REVIEW`. To also create the immutable canonical JSON Lines trace used
by evaluation, provide a local output directory and the exact commit being run:

```powershell
$commit = git rev-parse HEAD
python -m claim_agent.agent.integrated_runner `
    --pretty `
    --trace-directory '.\artifacts\traces' `
    --code-commit $commit
```

The generated `artifacts/traces/<run-id>.jsonl` file contains the reproducible
run configuration, redacted model and tool events, validation results, timing,
budget counters, and exactly one final outcome. Generated traces are local
artifacts and should not be committed.

## Run the knowledge and retrieval tests

These check that the manifest, source files, passage IDs, criteria, and per-procedure formulas in [`knowledge/`](knowledge/) are consistent, that policy matching and ranking behave as decision 0001 requires, and that the dispatcher validates requests and returns `ToolResult` records. The dispatcher tests need the agent dependencies in [`requirements.txt`](requirements.txt):

```bash
python -m pip install -r requirements.txt
PYTHONPATH=src python -m unittest discover -s tests/tools -p "test*.py" -v
```

PowerShell equivalent:

```powershell
$env:PYTHONPATH = '.\src'
py -3 -m pip install -r requirements.txt
py -3 -m unittest discover -s '.\tests\tools' -p 'test*.py' -v
```

Expected result:

```text
Ran 72 tests
OK
```

## Run the pilot evaluation

The [`evaluation/`](evaluation/) harness for the Milestone 2 pilot (#12) scores a compared system against labeled cases, one case per denied service line. Dataset A, rule and safety triage, is in [`datasets/development/set_a/`](datasets/development/set_a/), with draft single-labeler labels in [`datasets/labels/`](datasets/labels/) written with the [annotation rubric](datasets/ANNOTATION_RUBRIC.md). Only the frozen baseline can be run so far; the agent and single-call systems still need the hosted-model transport tracked in #34 and the generic evaluation entry point tracked in #12. The harness uses only the standard library.

From the repository root:

```bash
python -m evaluation.run_pilot \
  --manifest datasets/development/set_a/manifest.json \
  --labels datasets/labels/set_a_development.labeler_a.json \
  --experiment set-a-dev-baseline
python -m unittest discover -s tests/evaluation -p "test*.py" -q
```

Results go to `.local-output/eval/<experiment>/`: `predictions.jsonl`, `scores.json`, `results.md`, and `run_manifest.json`, which records the dataset and label hashes and the code commit. Rates are reported with counts and 95% Wilson intervals. Critical safety errors that need traces are reported as not measurable rather than zero. The labels are drafts until a second labeler and adjudication are complete, so these numbers are development checks, not results.

## Run one reproducible example

The following case is a synthetic brain MRI claim with a medical-necessity denial. The fixed `--as-of` date makes its deadline calculation reproducible.

```bash
python baseline/run_baseline.py \
  --input baseline/examples/test2_medical_necessity.json \
  --as-of 2026-09-06 \
  --outdir .local-output/baseline-smoke
```

PowerShell equivalent:

```powershell
py -3 '.\baseline\run_baseline.py' `
    --input '.\baseline\examples\test2_medical_necessity.json' `
    --as-of '2026-09-06' `
    --outdir '.\.local-output\baseline-smoke'
```

The expected primary result is:

```text
Disposition: APPEAL_WITH_DOCUMENTATION
Root cause: MEDICAL_NECESSITY
Deadline: 2027-01-07
```

The command writes a readable report and a structured JSON record to the selected output directory.

## Run all baseline examples

```bash
python baseline/run_baseline.py \
  --input baseline/examples \
  --as-of 2026-09-06 \
  --outdir .local-output/baseline-all \
  --quiet
```

This processes the six labeled example claims. Generated local outputs are ignored by Git.

## Baseline limitations

The baseline is intentionally narrow. It cannot retrieve or interpret payer medical policies, read clinical notes, resolve nuanced evidence conflicts, or adaptively request missing documents. Its filing windows are illustrative, its code mapping is a working subset, and its labels are a small synthetic development set.

These limitations define the work assigned to the agentic extension; they are not claims of real-world clinical or billing performance.

## Planned agentic extension

- [x] Freeze shared claim, evidence, tool, decision, and trace contracts and
  connect the integrated runner to durable canonical trace emission.
- [x] Wrap deterministic baseline findings and routing behind typed interfaces.
- [x] Implement a validated common tool dispatcher.
- [x] Add versioned payer-policy and encounter-note retrieval.
- [ ] Add eligibility retrieval if retained after the scope decision in #30.
- [x] Implement a bounded planning loop with structured model outputs behind scripted offline boundaries.
- [x] Connect typed routing, tool, validation, output, and canonical-trace adapters.
- [x] Add criterion-level evidence assessment and resolvable citations.
- [x] Add deterministic safety, citation, formula, and filing-deadline validation.
- [ ] Add provider-specific hosted transport, tool-less evidence verification,
  and grounded drafting through #34.
- [ ] Build development and held-out synthetic datasets.
- [ ] Compare the agentic system against the unchanged baseline.
- [ ] Conduct stakeholder review of representative system reports.

The primary evaluation will compare joint root-cause/disposition accuracy, selective accuracy, resolution coverage, citation precision, unsupported assertions, consistency, latency, cost, and critical safety failures.

## Repository structure

```text
baseline/                 Frozen deterministic comparison system
datasets/                 Development, held-out, and label artifacts
docs/reference/           Stakeholder proposal and project milestone
docs/decisions/           Architecture decision records
evaluation/               Evaluation and fault-injection harnesses
knowledge/                Versioned policy and synthetic evidence corpus
src/claim_agent/agent/    Bounded LLM orchestration
src/claim_agent/contracts Shared schemas and enums
src/claim_agent/output/   Reports and appeal preparation
src/claim_agent/safety/   Deterministic validation and human-review gates
src/claim_agent/tools/    Typed read-only tools and dispatcher
tests/                    Agentic-system tests organized by component
```

## Development guide

Team ownership, integration contracts, branch conventions, review responsibilities, and the phased implementation plan are documented in [the team development guide](docs/TEAM_DEVELOPMENT_GUIDE.md).

Every pull request must either update the relevant documentation or state why no documentation change is needed. Update this README when setup, commands, capabilities, limitations, architecture, layout, status, or roadmap claims change. Update the team guide when ownership, interfaces, dependencies, evaluation procedures, or collaboration rules change. After merge, the pull-request author verifies both documents against the new `main`; missed updates are fixed through a follow-up documentation pull request, never by pushing directly to protected `main`.

## Team

- Neha Kashyap
- Hriday Pradhan
- Silas Sangmin
- Sachin Kothandaraman

Developed as a CSE 598 Agentic AI capstone project at Arizona State University.

## Disclaimer

This repository is an educational prototype. It is not medical, legal, financial, coding, reimbursement, or payer-contract advice and must not be used to process real patient claims.
