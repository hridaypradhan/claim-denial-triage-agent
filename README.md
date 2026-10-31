# Claim Denial Triage Agent

A hybrid agentic AI system for evidence-grounded triage of denied outpatient medical claims. The project combines a deterministic Python core with bounded LLM tool use, evidence retrieval, safety validation, and human review.

> **Project status:** Active academic capstone. The deterministic stakeholder baseline is runnable and tested. The agentic extension currently has a repository skeleton but is not yet implemented.

## Problem

Outpatient practices lose recoverable revenue when denied claims are triaged late or incorrectly. A reviewer must determine why a claim was denied, what action is appropriate, what evidence is missing, and whether a filing deadline is still open.

This project is designed to produce a review package for an accounts-receivable specialist. It does **not** submit claims or appeals, connect to a live payer or EHR, or make an autonomous final decision.

## Intended system

The completed system will use a hybrid architecture:

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
    F --> G[(Policy, encounter, and eligibility evidence)]

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

The [`knowledge/`](knowledge/) directory contains the first frozen evidence snapshot for the planned retrieval tools:

- A [manifest](knowledge/manifest.json) listing every source with its version, payer, procedure codes or claim ID, effective dates, and origin.
- One synthetic medical policy for brain MRI (CPT 70551–70553) under the `BCBS_AZ` payer profile, with a criteria formula for each procedure code (`A AND B` for 70551 and 70552; `A AND B AND (C OR D)` for 70553). It is not the payer's actual policy.
- Two synthetic encounter documents for the example MRI claim `CLM-2026-005097`: a clinical note that documents criteria A and B but is silent on C and D, and a neurology progress note that documents C.

Every passage has a stable citation ID such as `POL-BCBSAZ-MRI-BRAIN@v1#P3`. The retrieval method and ID rules are recorded in [decision 0001](docs/decisions/0001-retrieval-method.md). No retrieval tool reads this corpus yet.

### Project scaffolding

The repository also contains the ownership boundaries for the planned system:

- [`src/claim_agent/contracts/`](src/claim_agent/contracts/) — shared data contracts.
- [`src/claim_agent/tools/`](src/claim_agent/tools/) — typed, read-only evidence tools.
- [`src/claim_agent/agent/`](src/claim_agent/agent/) — bounded agent state and orchestration.
- [`src/claim_agent/safety/`](src/claim_agent/safety/) — deterministic validation and approval gates.
- [`src/claim_agent/output/`](src/claim_agent/output/) — review reports and appeal preparation.
- [`knowledge/`](knowledge/) — versioned policies and synthetic evidence (corpus v0 above; eligibility records not yet added).
- [`datasets/`](datasets/) — development, held-out, and labeling artifacts.
- [`evaluation/`](evaluation/) — metrics, fault injection, and comparison harnesses.

These directories are scaffolding only unless listed under the implemented baseline above.

## Safety boundaries

- Only synthetic data is used. No protected health information is included.
- The system will not submit a claim, appeal, or patient bill.
- Every report begins in a human-review state.
- Missing, conflicting, unsupported, or unavailable evidence must lead to manual review.
- Retrieved policy and clinical text is treated as untrusted data, not as instructions.
- Illustrative payer rules must not be interpreted as verified contract terms.

## Requirements

The baseline requires:

- Python 3.9 or newer.
- No third-party packages.
- No model API key.
- No network connection.

The agentic extension will introduce separate dependencies later. Those dependencies will not be added to the frozen baseline.

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

## Run the knowledge corpus tests

These check that the manifest, source files, passage IDs, criteria, and per-procedure formulas in [`knowledge/`](knowledge/) are consistent:

```bash
python -m unittest discover -s tests/tools -p "test*.py" -v
```

PowerShell equivalent:

```powershell
py -3 -m unittest discover -s '.\tests\tools' -p 'test*.py' -v
```

Expected result:

```text
Ran 6 tests
OK
```

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

- [ ] Freeze shared claim, evidence, tool, decision, and trace contracts.
- [ ] Wrap deterministic baseline behavior behind stable interfaces.
- [ ] Implement a validated common tool dispatcher.
- [ ] Add versioned payer-policy, encounter-note, and eligibility retrieval.
- [ ] Implement a bounded planning loop with structured model outputs.
- [ ] Add criterion-level evidence assessment and resolvable citations.
- [ ] Add deterministic safety and deadline verification.
- [ ] Generate appeal drafts only from verified evidence.
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
