# Team Development Guide

This is the operating agreement for the four-person Claim Denial Triage Agent team. It assigns one directly responsible owner to every major area, defines cross-team handoffs, and sets the rules for merging and documenting work.

Assignments are proposed until all four members confirm them. Any ownership or interface change must update this file in the same pull request.

## Shared objective and boundaries

Build a bounded, auditable agent that resolves evidence-dependent denial cases without weakening the frozen deterministic baseline or introducing critical safety errors.

- `baseline/` is frozen at `baseline-v1`; changes require a team decision and a new baseline version.
- Use only synthetic records. Never place real patient or provider data in code, issues, prompts, traces, or screenshots.
- The system cannot submit claims, appeals, bills, or other external actions.
- Deadlines, allowed dispositions, citation resolution, and approval state remain deterministic.
- Missing, conflicting, inapplicable, or unsupported evidence produces `MANUAL_REVIEW`.
- Every output starts as `PENDING_REVIEW` and requires a human decision.
- Held-out results cannot be used to tune prompts, tools, schemas, or thresholds.

## Ownership summary

| Workstream | Primary owner | Required reviewer | Primary folders |
|---|---|---|---|
| Deterministic core and safety | Neha Kashyap | Sachin Kothandaraman | `src/claim_agent/contracts/`, `src/claim_agent/safety/`, `src/claim_agent/output/` |
| Evidence and retrieval tools | Silas Sangmin | Hriday Pradhan | `src/claim_agent/tools/`, `knowledge/` |
| Agent runtime and LLM workflow | Hriday Pradhan | Neha Kashyap | `src/claim_agent/agent/`, agent CLI and prompts |
| Evaluation and observability | Sachin Kothandaraman | Silas Sangmin | `datasets/`, `evaluation/`, evaluation scenarios |

Primary ownership means coordinating design, implementation, tests, and documentation in that area. The required reviewer must understand the change well enough to maintain it temporarily.

## Individual scopes

### Neha — Deterministic core and safety

**Owns**

- Shared contract definitions and contract-version changes.
- Adapter around the frozen baseline and deterministic rule/evidence routing.
- Deadline calculation, expiration handling, disposition allowlists, and patient-billing restrictions.
- Structural validation, citation-resolution checks, approval states, and final review-package validation.
- Report rendering after validated inputs are available.

**Delivers**

- `BaselineFinding`, validation results, and a deterministic route decision.
- Safety-validation functions with explicit failure and escalation reasons.
- A final `ReviewPackage` that is always `PENDING_REVIEW` until a human acts.

**Does not own**

- Policy search or evidence-ranking logic.
- Model prompts, planning behavior, or model-provider calls.
- Gold labels, metric definitions, or final evaluation reports.

**Must coordinate with**

- Hriday on `AgentDecision` validation and retry outcomes.
- Silas on citation and provenance fields.
- Sachin on safety-error definitions and evaluation assertions.

### Silas — Evidence and retrieval tools

**Owns**

- Common tool dispatcher and argument validation at the tool boundary.
- Fake dispatcher with scripted tool results for the offline vertical slice.
- Denial-code and payer-profile lookup wrappers; payer profiles report their version and whether values are verified or illustrative.
- Policy retrieval, encounter-note retrieval, and eligibility-record retrieval. All tools are read-only.
- Policy matching that filters by payer, procedure, and service date before ranking, with no fallback to another payer's policy or a different effective date.
- Encounter notes returned as numbered, citable passages, with unavailable documents reported explicitly.
- Eligibility records returned without deciding eligibility. This tool is cut first if the schedule slips.
- Knowledge manifests, passage IDs, provenance, and no-match/error responses.
- Policy corpus selection and source metadata: URL, access date, effective date, payer, procedure, section, and version. A policy is included only when its terms of use permit.
- The retrieval-method architecture decision.

**Delivers**

- Typed `ToolResult` records containing status, data, source IDs, and error codes.
- Versioned `EvidencePassage` records that can be resolved deterministically.
- A fake dispatcher for Hriday's offline vertical slice.
- Tests for success, missing data, unavailable documents, no match, incompatible policy, malformed request, transient failure, and timeout.

**Does not own**

- The agent’s decision, planning loop, or tool-selection prompt.
- Final appeal eligibility or safety approval.
- Dataset labels or performance thresholds.

**Must coordinate with**

- Neha on tool contracts and citation validation.
- Hriday on dispatcher inputs, outputs, and retry semantics.
- Sachin on frozen knowledge snapshots and retrieval metrics.

### Hriday — Agent runtime and LLM workflow

**Owns**

- Provider-independent model interface and scripted fake model.
- Agent state, deterministic state updates, planning loop, and model/tool budgets.
- Tool selection, structured model output, invalid-output repair, and trace emission.
- Separate tool-less evidence-verification and appeal-drafting model calls.
- Agent CLI and the offline vertical-slice runner.

**Delivers**

- A schema-valid `AgentDecision` with actions, criterion assessments, evidence gaps, rationale, and citations.
- Append-only `TraceEvent` records for model turns, tool calls, observations, repairs, and terminal states.
- Explicit `MANUAL_REVIEW` results for exhausted budgets or unresolved evidence.

**Does not own**

- Deadline arithmetic, final disposition validation, or approval state.
- Tool internals, knowledge-corpus content, or provenance generation.
- Gold labels, dataset splits, or final metric calculations.

**Must coordinate with**

- Neha on contracts, safety rejection, and final review-package inputs.
- Silas on dispatcher behavior and tool-error recovery.
- Sachin on trace fields, repeat-run configuration, latency, token, and cost capture.

### Sachin — Evaluation and observability

**Owns**

- Synthetic case templates, split manifests, annotation rubric, and label storage.
- Development and held-out evaluation runners.
- Joint accuracy, selective accuracy, resolution coverage, citation precision, consistency, latency, cost, and safety metrics.
- Fault injection, error analysis, confusion matrices, and stakeholder-review packages.

**Delivers**

- Frozen development and held-out manifests with leakage controls.
- Pre-run gold labels and documented disagreement resolution.
- Baseline-versus-agent reports with category-level results and critical-safety-error counts.

**Does not own**

- Runtime prompts, model repair behavior, or tool implementation.
- Deterministic safety policy or final report-generation code.
- Unilateral held-out labels; every held-out case requires two independent labelers.

**Must coordinate with**

- Neha on safety assertions and expected escalation behavior.
- Silas on retrieval relevance and provenance metrics.
- Hriday on trace, repeatability, latency, token, and cost fields.

## Gap-prevention responsibility matrix

| Deliverable | Directly responsible owner | Required input or approval |
|---|---|---|
| Shared contracts v0.1 | Neha | All affected owners approve breaking fields |
| Baseline adapter and deterministic router | Neha | Baseline regression tests |
| Tool dispatcher and knowledge manifest | Silas | Contract approval from Neha; runtime review from Hriday |
| Policy, encounter, and eligibility tools | Silas | Evaluation provenance requirements from Sachin |
| Fake dispatcher for the offline vertical slice | Silas | Tool contracts from Neha; consumer review from Hriday |
| Policy corpus selection and source metadata | Silas | Payer/procedure coverage needs from Sachin |
| Fake model and offline vertical slice | Hriday | Fake dispatcher from Silas; validators from Neha |
| Hosted-model runtime and prompts | Hriday | Stable tool and decision contracts |
| Final validators and review package | Neha | Agent decision from Hriday; resolvable sources from Silas |
| Trace schema implementation | Hriday | Required fields specified with Sachin |
| Dataset, labels, metrics, and fault injection | Sachin | Safety rules from Neha; tool/runtime failure modes from Silas and Hriday |
| End-to-end evaluation report | Sachin | Frozen versions of all components |
| Public README accuracy | PR author | Reviewer verifies public claims and commands |
| This team guide | Affected owner | All affected owners approve scope/process changes |

The following cross-team work remains unassigned: the `calculate_deadline` tool wrapper, transient-failure retry ownership, the per-claim document inventory, and untrusted-text handling in tool results. Each must be recorded in a GitHub issue before implementation and added to this table only after the affected owners agree.

If work does not clearly fit one row, open an issue and assign an owner before implementation. Do not assume another workstream will cover it.

## Interface and handoff rules

Components communicate through versioned contracts, not each other’s internal objects.

| Producer | Consumer | Handoff is accepted when |
|---|---|---|
| Neha | Hriday | Baseline finding and route decision validate against contract tests |
| Silas | Hriday | Tool results include valid status, typed data, stable source IDs, and explicit errors |
| Hriday | Neha | Agent decision is schema-valid and contains only resolvable citation IDs |
| Neha | Sachin | Review package has deterministic validation status and explicit escalation reasons |
| All components | Sachin | Trace and output fields required by the frozen evaluation schema are present |

Unknown or missing values must be explicit. No component may replace missing information with a guessed default.

## Implementation and merge sequence

1. Shared contracts and contract tests.
2. Baseline adapter and deterministic router.
3. Fake model, fake dispatcher, and offline vertical-slice test.
4. Real denial-code, payer-profile, deadline, policy, encounter, and eligibility tools.
5. Bounded agent runtime and trace logging.
6. Deterministic validation, tool-less verification, and grounded drafting.
7. Development dataset, fault injection, and evaluation harness.
8. Frozen held-out evaluation and stakeholder review.

Later work may be developed behind fake interfaces, but it cannot merge before its prerequisite contract is stable.

## Dataset and evaluation rules

1. Write the annotation rubric before producing system outputs.
2. Use the original six cases only for development and regression.
3. Create at least 20 additional development cases and 80 held-out cases.
4. Keep scenario variants and counterfactual pairs in the same split.
5. Assign two independent labelers to each held-out case and resolve disagreements before reviewing outputs.
6. Freeze splits, labels, model, prompt, parameters, budgets, and knowledge snapshot before final evaluation.
7. Run the baseline once and the agent three times per held-out case.
8. Never tune against held-out failures; preserve them for final analysis.

## Git and pull-request workflow

- Protect `main`; all changes merge through reviewed pull requests.
- Start every branch from an updated `main` and link it to one issue.
- Use short-lived branches such as `feat/agent-runtime`, `feat/policy-retrieval`, or `eval/joint-decision-metrics`.
- Keep commits focused; do not mix unrelated schema, runtime, dataset, and documentation changes.
- The primary owner and required reviewer from the ownership table must review the pull request.
- Shared contracts and integration tests require one additional affected-workstream review.
- Squash merge and delete the branch after merge.

## Documentation update rule

Documentation is part of the feature, not a later cleanup task.

### Before merge

Every pull request must include one of the following in its description:

```text
Documentation impact: README updated
Documentation impact: Team guide updated
Documentation impact: README and team guide updated
Documentation impact: None — <specific reason>
```

Update `README.md` in the same pull request when the change affects public facts:

- Setup, requirements, commands, or output examples.
- Implemented capabilities, limitations, safety boundaries, architecture, or repository layout.
- Roadmap checkboxes or project-status statements.

Update this team guide in the same pull request when the change affects internal coordination:

- Ownership, scope, reviewers, folders, or handoffs.
- Shared contracts, dependencies, merge order, milestones, evaluation procedure, or team workflow.

Internal refactors with no command, behavior, ownership, interface, or status change may use `Documentation impact: None`, but the reviewer must confirm the reason.

### After a successful merge to `main`

The PR author must:

1. Pull the updated `main` and verify the documented commands still work.
2. Check that README status and roadmap claims match what actually merged.
3. Check that this guide still reflects current ownership and interfaces.
4. Verify GitHub renders Markdown links, tables, and Mermaid diagrams correctly.

If anything is stale, create a short `docs/...` branch and corrective pull request immediately. Do not patch documentation directly on protected `main`.

## Pull-request completion checklist

- [ ] Acceptance criteria are met and the change stays within the assigned scope.
- [ ] Focused tests and baseline regression tests pass.
- [ ] Relevant offline integration and failure-path tests pass.
- [ ] Contracts are versioned and handoff behavior is documented.
- [ ] No secrets, real patient data, generated traces, or local outputs are committed.
- [ ] README/team-guide impact is handled or explicitly justified.
- [ ] The required owner and cross-workstream reviewers approve.
- [ ] The branch is squash-merged and deleted.
- [ ] The author completes the post-merge documentation check.

## Architecture decisions and communication

Record cross-workstream decisions under `docs/decisions/` with context, considered options, decision, consequences, and affected contracts. Initial decisions include the schema library, model-provider interface, retrieval method, prompt/model versioning, trace format, and dataset-freeze procedure.

Hold two brief progress syncs and one integration demonstration per week. Each owner reports completed work, next dependency, and blockers using tested behavior rather than file counts. Decisions that affect another workstream must be recorded in an issue or architecture decision, not only in chat.

## Immediate shared backlog

1. Decide the agent-side schema library.
2. Define contract version `0.1` and contract rejection tests.
3. Implement the baseline adapter and deterministic router.
4. Implement the scripted fake model and fake dispatcher.
5. Add the offline vertical-slice integration test.
6. ~~Select the initial payer/procedure policy subset~~ — done: BCBS_AZ brain MRI (knowledge corpus v0). Next: add a second payer/procedure pair and an effective-date test case (Silas).
7. Draft the dataset annotation rubric.

Do not replace fake boundaries with hosted-model calls or production retrieval until the offline vertical slice passes.
