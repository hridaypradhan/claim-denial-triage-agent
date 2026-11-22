# Architecture

## Design goal

The system assists with synthetic claim-denial review while keeping safety-
critical behavior deterministic. The language model can interpret evidence and
propose work, but it cannot expand its own authority, approve an output, or
perform an external action.

## Execution flow

1. **Validate the claim.** Shared contracts reject malformed or inconsistent
   claim and service-line data.
2. **Run baseline rules.** The frozen baseline identifies a root cause,
   preliminary disposition, deadline, and required evidence.
3. **Choose a route.** Rule-decidable cases remain deterministic. Evidence-
   dependent cases enter the bounded agent. Cases missing decision-critical
   evidence go directly to manual review.
4. **Plan and retrieve.** The planner may call an authorized read-only tool,
   propose a complete decision, or request manual review. Tool requests are
   bound to the active claim and route.
5. **Build same-run evidence.** Successful tool observations enter an immutable
   evidence bundle. Failed calls do not become evidence.
6. **Validate deterministically.** Code checks identity, route, disposition,
   deadline, policy formula, required evidence, and citation resolution.
7. **Verify without tools.** A separate model call compares an accepted proposal
   with the frozen evidence bundle. Any non-agreement or failure requires manual
   review.
8. **Draft when authorized.** Only an accepted, verified, appealable decision can
   reach the drafter. Code reconstructs the letter from validated grounded
   claims and citations rather than trusting free-form prose.
9. **Package for a person.** Every path creates an audited review package with
   `PENDING_REVIEW`. Nothing is submitted automatically.

## Component map

| Area | Location | Responsibility |
|---|---|---|
| Shared contracts | `src/claim_agent/contracts/` | Versioned claim, decision, evidence, validation, and output types |
| Routing and runtime | `src/claim_agent/agent/` | State machine, budgets, tool requests, repairs, traces, and runners |
| Hosted boundary | `src/claim_agent/agent/hosted/` | Clients, strict schemas, Voyager configuration, and OpenAI-compatible transport |
| Verification | `src/claim_agent/agent/verification/` | Tool-less verifier request, execution, and trace records |
| Drafting | `src/claim_agent/agent/drafting/` | Post-decision authorization, grounded drafting, and trace records |
| Evidence tools | `src/claim_agent/tools/` | Read-only policy, note, eligibility, deadline, and reference lookups |
| Safety | `src/claim_agent/safety/` | Required-evidence, decision, verifier, and draft validation |
| Output | `src/claim_agent/output/` | Final human-review package assembly |
| Knowledge | `knowledge/`, `eligibility/` | Versioned synthetic policies, passages, records, manifests, and locks |
| Evaluation | `evaluation/`, `datasets/` | Frozen plans, labels, metrics, experiment runners, and evidence |

The files directly under `agent/` form one runtime layer. Subpackages separate
the three hosted roles—planning, verification, and drafting—without introducing
provider-specific business logic.

## Hosted-model boundary

Voyager is a provider configuration for one reusable OpenAI-compatible
transport, not a separate transport per model. The accepted profile fixes the
provider, `beam-1-0` model, structured-response mode, prompt versions, request
limits, and disabled gateway additions. Credentials are read from
`CLAIM_AGENT_LLM_API_KEY` and never enter run configuration or traces.

Planning, verification, and drafting use separate schemas and limits, but all
calls use the same transport boundary. The final experiment used a 60-second,
2,048-token limit for planning and verification and a reviewed 180-second,
4,096-token limit for drafting.

The provider and selection rationale are in
[ADR 0003](decisions/0003-hosted-model-provider-and-selection.md).

## Deterministic controls

- **Authorization:** a route-derived allowlist controls which tools can be
  called. Arguments must match the active claim.
- **Budgets:** model calls, tool calls, retries, steps, and elapsed time have
  explicit limits.
- **Evidence isolation:** citation IDs must resolve in the current run's frozen
  evidence bundle.
- **Policy logic:** Boolean policy criteria use a purpose-built evaluator, not
  executable expressions.
- **Deadline safety:** expired or unsupported deadlines cannot be overridden by
  a model.
- **Output safety:** rejected decisions cannot enter an accepted package. The
  approval state is always `PENDING_REVIEW`.
- **Failure handling:** no hosted failure falls back to scripted behavior or a
  second model. The case is explained and sent to manual review.

## Audit records

The runtime emits canonical JSON Lines events for configuration, model calls,
tool calls, validation, verifier and drafter outcomes, budgets, timing, and one
terminal result. Secrets, raw provider reasoning, and raw appeal prose are not
stored. Stable event definitions and hashing rules are in
[ADR 0002](decisions/0002-trace-and-run-config.md).

## Deliberate boundaries

The repository does not implement live EHR, payer, clearinghouse, or billing-
system connections; autonomous approval or submission; write-capable tools; a
complete payer-policy corpus; real patient data; or production validation.

Scripted models remain for repeatable offline tests. They are test fixtures,
not a production fallback.
