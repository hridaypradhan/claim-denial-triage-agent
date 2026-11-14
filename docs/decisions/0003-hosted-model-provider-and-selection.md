# 0003: Hosted model provider, selection, and failure policy

- **Status:** Proposed
- **Date:** 2026-11-13
- **Issue:** #48
- **Owner:** Hriday Pradhan
- **Required reviewer:** Neha Kashyap
- **Consulted:** Sachin Kothandaraman (repeat-run evidence), Silas Sangmin (citation preservation)
- **Affected components:** hosted-model transport, planning prompt, run configuration, canonical trace, hosted-run documentation

## Context

The bounded runtime already depends on a provider-neutral `ModelClient` and a
replaceable `HostedModelTransport`. Issue #48 must connect that boundary to one
real hosted model without making normal tests require credentials or network
access. ASU Research Computing provides students with a no-charge,
OpenAI-compatible gateway at `https://openai.rc.asu.edu/v1` and exposes model
capabilities, request usage, and model status through Voyager.

The selected model must reliably choose one permitted structured action,
preserve evidence and citation identifiers, and remain subordinate to the
runtime's deterministic validation. General chat scores alone are not enough
to select it. The final evaluation also requires a reproducible model and
configuration rather than automatic routing among models.

## Decisions

### Provider and transport boundary

- Use ASU Research Computing Voyager as the initial hosted provider.
- Implement one reusable OpenAI Chat Completions-compatible transport behind
  `HostedModelTransport`. Voyager is configuration for that transport, not a
  model-specific transport class.
- Keep the base URL, provider name, exact model ID, prompt version, model
  parameters, request timeout, and retry policy in explicit configuration.
- Read each developer's credential from `CLAIM_AGENT_LLM_API_KEY`. Never store
  a key in source, committed configuration, traces, screenshots, or test
  fixtures.
- Each team member uses an individual Voyager key. Normal automated tests use
  a fake transport and require no key or network access.
- Disable gateway-added capabilities with `x-obleth-boons: off`, do not use
  `model="auto"`, and do not enable provider-hosted tools. The local runtime
  remains the only authority allowed to execute an allowlisted tool.

### Failure and fallback policy

- A timeout, capacity error, authentication error, provider error, incomplete
  response, empty response, or exhausted repair budget fails closed to an
  audited `MANUAL_REVIEW` result.
- The scripted model is only a test and demonstration fixture. It is never a
  production fallback because its responses are predefined and do not
  generalize to an arbitrary claim.
- Do not switch automatically to another hosted model. A model change alters
  evaluated behavior and would make the run configuration ambiguous.
- Record the attempted provider, model, request identity when available,
  elapsed time, token usage when reported, failure category, and terminal
  outcome in the canonical trace without persisting credentials or
  unrestricted provider error text.
- A failed final-evaluation run remains a recorded failure. Any retry uses the
  same frozen configuration under the rerun policy defined before evaluation.

### Model-selection procedure

`mimo-v2-6-pro` was the initial integration model because Voyager currently
lists both Tools and Schema capabilities, a 262.1K context window, and a strong
general chat result. It is not the accepted final model until the development
comparison is complete.

The v4 comparison freezes these candidates on development data only:

1. `qwen35-397b-a17b`
2. `qwen36-27b`
3. `beam-1-0`

Before running the comparison, freeze the cases, prompt version, action schema,
tool allowlists, budgets, repetitions, scoring rules, and tie breakers. Run each
candidate three times on six to ten representative synthetic development
cases. Candidate-specific supported features may be used because the decision
selects a deployable model configuration rather than an isolated base model.
Gateway-added capabilities remain disabled for every candidate.

The first `mimo-v2-6-pro` smoke run under `hosted-comparison-v1` confirmed the
provider connection and strict action parsing, but it was not a valid model
comparison result. The model had no trusted way to discover valid encounter
document IDs, attempted three invalid IDs, and reached the 30-second whole-run
limit. `hosted-comparison-v2` corrects the integration boundary by supplying
claim-bound document IDs and policy lookup fields from the knowledge manifest.
It also raises the whole-run limit to 180 seconds while retaining a 30-second
limit per provider request. The v1 artifact remains diagnostic evidence and
must not be included in candidate scoring.

The v2 smoke run then retrieved the policy and both valid encounter documents
successfully. Its first three model calls completed in about 10, 17, and 24
seconds, while the fourth call exceeded the 30-second request limit by about
68 milliseconds. The run remained well below its 180-second whole-run limit.
`hosted-comparison-v3` therefore applies a 60-second request limit uniformly
to every candidate and a 300-second whole-run limit. This is an operational
budget correction made before candidate comparison; the v2 artifact is also
diagnostic evidence and must not be scored.

The v3 smoke gate ran the same case and repeat once for every provisional
candidate. `mimo-v2-6-pro` continued to produce valid structured actions and
valid claim-bound tool calls, but a later request exceeded the uniform
60-second limit. Both `glm-5-3` and `deepseek-v4-1-flash` returned an invalid
initial action and an invalid bounded repair without calling a tool. Every run
failed closed to `MANUAL_REVIEW`, and no critical safety failure occurred.
Because no candidate completed one validated end-to-end decision, the
remaining v3 runs are paused. These three runs are compatibility diagnostics,
not model-selection evidence. The candidate set must be reviewed for explicit
structured-schema support before a new comparison plan is frozen.

Voyager's Schema-filtered catalog was then reviewed directly rather than
inferring compatibility from the badges visible in an unfiltered list. V4
selects three candidates that explicitly expose Tools and Schema and score
above 92% on Voyager's chat certification. The two Qwen candidates add strong
instruction-following evidence at different model sizes; Beam adds model-family
diversity and the highest chat score of the three. Every v4 candidate uses the
same strict `json_schema` response mode. `mimo-v2-6-pro` remains diagnostic
evidence for schema compliance but is excluded after repeated timeouts;
Ornith remains a reserve with no completed agent-with-tools result; Granite is
excluded because its measured agent-with-tools result is materially weaker.
V4 must pass the one-case smoke gate before its remaining runs are authorized.

The first v4 `qwen36-27b` smoke returned a valid strict-schema policy-tool
action, but the provider reported 1,687 output tokens and about 60.6 seconds
for that action; the following request timed out. Voyager documents that model
thinking counts toward the completion-token budget. V5 therefore sets
`reasoning_effort` to `none` and `max_completion_tokens` to 2,048 uniformly
for all candidates while retaining the 60-second request limit. This tests a
deployable low-latency configuration before candidate comparison. The v4
artifact remains compatibility evidence and must not be scored.

All three v5 candidates produced valid strict-schema actions and valid tool
calls, but none produced the adjudicated decision. Beam completed without a
provider failure and requested manual review after retrieving the policy and
one encounter note even though a second relevant document was listed and one
tool call remained. `planning-v2` therefore requires the model to retrieve
relevant, still-unqueried, claim-bound documents before requesting review only
for missing or insufficient evidence, when authorization and budget remain.
It also forbids repeated successful calls and irrelevant retrieval. V6 freezes
this provider-neutral prompt correction; all v5 artifacts remain diagnostic
and must not be scored.

The v6 smoke gate exposed a scorer defect after Beam and Qwen 27B both reached
the adjudicated root cause and disposition. Beam cited adjudicated evidence for
all four criterion findings. Qwen 27B cited matching adjudicated evidence for
only two criteria, while using a different retrieved passage for one criterion
and no criterion citation for another. The original citation metric counted
both runs as perfect because it measured citation resolvability rather than
criterion-level semantic support. V7 preserves the frozen candidates, prompt,
budgets, and provider parameters while adding a criterion-evidence support
rate: each labeled criterion counts only when the predicted status matches and
at least one citation belongs to that criterion's adjudicated passage set.
This permits legitimate alternative labeled passages and prevents unrelated
retrieved evidence from receiving full grounding credit. V6 artifacts are
diagnostic and must not be combined with v7 comparison results.

The v7 smoke gate then ran one identical development case through all three
candidates. Beam was the only candidate to satisfy the complete gate: correct
root cause and disposition, four of four criterion findings grounded in
matching adjudicated passages, valid schema and tool calls, and zero provider
or critical safety failures. Qwen 27B again grounded only two of four criteria
because it did not retrieve the second encounter note. Qwen 397B retrieved the
policy and both notes but timed out on the final decision request, repeating
its earlier operational failure. V8 removes both failed candidates and freezes
Beam for the 24-run development validation: eight cases, three repetitions,
and otherwise unchanged prompt, budgets, provider settings, and ranking rules.
This is a gate-based reduction, not a post-hoc winner declaration; the v8
validation must complete before the model decision is accepted.

The first v8 missing-evidence case exposed a scoring denominator defect. Two
correct model-requested manual reviews contained no proposed decision, yet the
criterion metric counted every absent assessment as a grounding failure. V9
retains the Beam configuration and 24-run validation but scores criterion
grounding only when a decision is proposed; direct manual-review behavior is
already measured separately. For an adjudicated `UNKNOWN` criterion with no
supporting passage, a proposed assessment counts only when it is also
`UNKNOWN` and uncited. This preserves a penalty for unsupported `NOT_MET`
claims while avoiding a penalty for correct fail-closed behavior. V8 artifacts
are diagnostic and must not be combined with v9 validation results.

Rank results in this order:

1. critical safety failures,
2. valid action/schema rate,
3. correct route or tool selection,
4. evidence-grounded decision quality,
5. correct and resolvable citations,
6. correct manual review when evidence is insufficient,
7. repeated-run stability,
8. provider reliability and latency,
9. token usage.

Use safety, grounded accuracy, schema reliability, stability, and then latency
as tie breakers. Do not inspect held-out final labels or use final-evaluation
results to tune or select the model. Update this record to **Accepted** with the
result table, exact winning model ID, prompt version, and frozen parameters
before issue #66 begins.

## Consequences

- Provider integration remains replaceable without adding a transport class
  for every model.
- Hosted failures are visible and safe instead of being concealed by scripted
  or cross-model fallback.
- Model selection adds a small development experiment to #48 but produces an
  evidence-based, auditable choice before the held-out evaluation.
- Voyager model retirement or replacement requires a new documented
  development comparison; it must not silently change an in-progress frozen
  evaluation.
- The README must document credential setup, offline and hosted commands,
  frozen configuration, failure behavior, and the synthetic-data restriction
  as the implementation lands.
