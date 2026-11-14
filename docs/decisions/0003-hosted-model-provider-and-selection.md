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

`mimo-v2-6-pro` is the provisional integration model because Voyager currently
lists both Tools and Schema capabilities, a 262.1K context window, and a strong
general chat result. It is not the accepted final model until the development
comparison is complete.

Compare these fixed candidates on development data only:

1. `mimo-v2-6-pro`
2. `glm-5-3`
3. `deepseek-v4-1-flash`

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
