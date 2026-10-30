# 0001: Trace events and agent run configuration

- **Status:** Proposed
- **Date:** 2026-10-29
- **Issue:** #7
- **Requirements owner:** Sachin Kothandaraman (evaluation)
- **Runtime producer and required reviewer:** Hriday Pradhan
- **Input requested from:** Silas Sangmin (tool events), Neha Kashyap (validation events)
- **Related:** #3 (`ToolResult`, `EvidencePassage`), #5 (`BaselineFinding`, `RouteDecision`), #6 (`AgentDecision`, `ValidationResult`)

## Context

The evaluation must be able to do three things with every agent run:

- run it again with the same settings,
- measure its cost, speed, and consistency,
- explain why it stopped.

To do that, the runtime must write a fixed trace format (`TraceEvent`) and record the settings it ran with (`AgentRunConfig`). This document answers the ten decisions listed in #7. Field names follow the proposal in #7. Payload contents that belong to other contracts (#3, #5, #6) are referenced here, not redefined.

## Decision summary

| # | Question in #7 | Decision |
|---|---|---|
| 1 | Which fields are required on every event? | Eight fields are always required. The rest are required only for certain event types (see [TraceEvent fields](#traceevent-fields)). |
| 2 | Is append-only JSON Lines required? | Yes. Each run writes one UTF-8 `.jsonl` file with one event per line, and lines are never edited. |
| 3 | Store full prompts and evidence, or reference them? | Reference them. Store the prompt version and a hash of the rendered prompt, and the evidence passage IDs. Store the model's structured response in full. |
| 4 | What must be excluded or redacted? | Secrets, credentials, request headers, environment variables, and local machine paths. Claim content stays out of traces (see [Redaction and data safety](#redaction-and-data-safety)). |
| 5 | Timestamps, or sequence numbers only? | Both are written. Ordering and deterministic tests use `sequence_number` only. Timestamps and durations are left out of exact-match comparisons. |
| 6 | Who calculates cost? | The evaluation harness, from token counts and a versioned price table. The runtime records tokens only. |
| 7 | Which terminal and manual-review reason codes? | A fixed list (see [Terminal reasons](#terminal-reasons)). Each run ends with exactly one terminal event. |
| 8 | Which model parameters are frozen for repeat runs? | Every field in `AgentRunConfig`, plus the code commit (see [AgentRunConfig](#agentrunconfig)). |
| 9 | How is unavailable token or cost data represented? | `null` together with a `usage_status` code. Never `0` or a guessed value. |
| 10 | Which latency is measured? | All three: model call, tool call, and total run. Each uses a monotonic clock. |

## Storage format

- One file per run, named `<run_id>.jsonl`, encoded as UTF-8.
- One JSON object per line, written in order. Lines are never edited or deleted after they are written.
- `sequence_number` starts at 1 and increases by 1 with no gaps.
- The first event is always `RUN_STARTED`. The last event is always exactly one terminal event: `RUN_COMPLETED` or `MANUAL_REVIEW`.
- Generated traces are local outputs and are not committed. Only hand-written examples under `evaluation/examples/` are committed.

## TraceEvent fields

### Required on every event

| Field | Type | Notes |
|---|---|---|
| `trace_version` | string | `"0.1"` for this version |
| `run_id` | string | Unique for each run |
| `case_id` | string | The claim ID; joins to the gold label |
| `sequence_number` | int | Ordering key, starting at 1 |
| `event_type` | enum | See [Event types](#event-types) |
| `component` | enum | `AGENT_RUNTIME`, `MODEL_CLIENT`, `TOOL_DISPATCHER`, or `SAFETY_VALIDATOR` |
| `timestamp` | string | UTC ISO 8601. Recorded for people to read; not used for ordering |
| `payload` | object | Event-specific content. `{}` if there is none |

### Required for some event types

| Field | Required on | Notes |
|---|---|---|
| `request_id` | `MODEL_REQUESTED`, `MODEL_RESPONDED`, `MODEL_OUTPUT_REJECTED`, `REPAIR_ATTEMPTED`, `TOOL_REQUESTED`, `TOOL_RETURNED` | Pairs a request with its response |
| `model_id` | `MODEL_REQUESTED`, `MODEL_RESPONDED` | Exact pinned model version |
| `prompt_version` | `MODEL_REQUESTED` | Frozen prompt version |
| `elapsed_ms` | `MODEL_RESPONDED`, `TOOL_RETURNED`, terminal events | Model latency, tool latency, or total run time |
| `input_tokens`, `output_tokens` | `MODEL_RESPONDED` | Integer, or `null` with a `usage_status` |
| `usage_status` | `MODEL_RESPONDED` | `REPORTED`, `NOT_REPORTED_BY_PROVIDER`, or `NOT_APPLICABLE` (scripted fake model) |
| `terminal_reason` | `RUN_COMPLETED`, `MANUAL_REVIEW` | See [Terminal reasons](#terminal-reasons) |

Fields that do not apply to an event are omitted, not set to `null`. `null` always means "this applies but the value is unavailable".

### Change from the #7 proposal

`estimated_cost` is removed from `TraceEvent`. Cost is calculated by the evaluation harness (decision 6), so the runtime never writes it. Removing the field prevents two different cost numbers from existing for the same run.

## Event types

| Event type | Component | Payload must include |
|---|---|---|
| `RUN_STARTED` | `AGENT_RUNTIME` | The full `AgentRunConfig`, `config_sha256`, `route_decision_id` (#5), and an optional `evaluation_context` with `experiment_id` and `repeat_index` |
| `MODEL_REQUESTED` | `MODEL_CLIENT` | `call_index`, `prompt_sha256`, the evidence `passage_ids` included in context, and `repair_attempt` (0 if this is not a repair) |
| `MODEL_RESPONDED` | `MODEL_CLIENT` | The structured response: the requested tool calls, or a proposed decision |
| `MODEL_OUTPUT_REJECTED` | `AGENT_RUNTIME` | `rejection_code` and the schema errors |
| `REPAIR_ATTEMPTED` | `AGENT_RUNTIME` | `repair_index` and the rejected `request_id` it repairs |
| `TOOL_REQUESTED` | `TOOL_DISPATCHER` | `tool_call_index`, `tool_name`, and `tool_args` |
| `TOOL_RETURNED` | `TOOL_DISPATCHER` | The `ToolResult` fields from #3: `status`, `error_code`, and `source_ids`, but not full passage text |
| `DECISION_PROPOSED` | `AGENT_RUNTIME` | The `AgentDecision` from #6, with `citation_ids` |
| `VALIDATION_RETURNED` | `SAFETY_VALIDATOR` | The `ValidationResult` from #6: `passed`, the check results, and the escalation reasons |
| `RUN_COMPLETED` | `AGENT_RUNTIME` | `final_disposition`, `final_root_cause`, `approval_state` (always `PENDING_REVIEW`), and `counters` |
| `MANUAL_REVIEW` | `AGENT_RUNTIME` | `manual_review_reasons` (one or more), `approval_state` (always `PENDING_REVIEW`), and `counters` |

`counters` records how many model calls, tool calls, repairs, and total steps were used: `model_calls`, `tool_calls`, `repairs`, `total_steps`.

## Terminal reasons

`terminal_reason` holds the main reason. `MANUAL_REVIEW` events also list every reason that applied in `manual_review_reasons`.

| Code | Terminal event | Meaning |
|---|---|---|
| `COMPLETED_VALIDATED` | `RUN_COMPLETED` | A decision was proposed and passed every deterministic validation check |
| `MODEL_CALL_BUDGET_EXHAUSTED` | `MANUAL_REVIEW` | The `max_model_calls` limit was reached |
| `TOOL_CALL_BUDGET_EXHAUSTED` | `MANUAL_REVIEW` | The `max_tool_calls` limit was reached |
| `REPAIR_BUDGET_EXHAUSTED` | `MANUAL_REVIEW` | The `max_repairs` limit was reached and model output was still invalid |
| `STEP_BUDGET_EXHAUSTED` | `MANUAL_REVIEW` | The `max_total_steps` limit was reached |
| `TIMEOUT` | `MANUAL_REVIEW` | The `timeout_seconds` limit was reached |
| `VALIDATION_FAILED` | `MANUAL_REVIEW` | A proposed decision failed a deterministic check |
| `MISSING_EVIDENCE` | `MANUAL_REVIEW` | The evidence required for the decision was not found |
| `CONFLICTING_EVIDENCE` | `MANUAL_REVIEW` | Sources disagreed and the conflict could not be resolved |
| `POLICY_NOT_APPLICABLE` | `MANUAL_REVIEW` | No policy applied on the date of service |
| `TOOL_UNAVAILABLE` | `MANUAL_REVIEW` | A required tool kept failing after its retries |
| `DEADLINE_EXPIRED` | `MANUAL_REVIEW` | The filing deadline for the recoverable action had passed |
| `MODEL_REQUESTED_REVIEW` | `MANUAL_REVIEW` | The model itself chose manual review |
| `RUNTIME_ERROR` | `MANUAL_REVIEW` | An unexpected runtime error occurred. The run still ends in a safe state |

Every budget, evidence, and validation code maps directly to a category in the evaluation error analysis.

## AgentRunConfig

All of these fields are frozen before the held-out evaluation. A repeat run must use exactly the same values. The whole config is written into the `RUN_STARTED` payload, and `config_sha256` is a hash of it.

| Field | Type | Notes |
|---|---|---|
| `config_version` | string | **Added.** Version of this config format |
| `code_commit` | string | **Added.** Git commit of the code that ran |
| `model_provider` | string | |
| `model_id` | string | Exact dated model version, never an alias |
| `model_parameters` | object | Every sampling parameter that is sent: at least `temperature`, `top_p`, and `max_output_tokens`. A parameter that is not sent is listed as `null` |
| `prompt_version` | string | |
| `random_seed` | int | Used by the scripted fake model and any sampling the runtime controls. Hosted models may not honor it, which is why consistency is measured across three runs |
| `max_model_calls` | int | Repair calls count toward this |
| `max_tool_calls` | int | |
| `max_repairs` | int | |
| `max_total_steps` | int | Model calls plus tool calls |
| `timeout_seconds` | int | Total wall-clock time for the run |
| `knowledge_snapshot_id` | string | Frozen knowledge corpus version |

## Redaction and data safety

- **Never written:** API keys, tokens, authorization headers, environment variables, provider endpoint URLs containing credentials, and local file paths or usernames.
- **Referenced, not copied:** the rendered prompt (`prompt_version` plus `prompt_sha256`), evidence text (`passage_ids` and `source_ids` only), and claim content (`case_id` only).
- **Stored in full:** the model's structured response and the validation results. These are needed for error analysis and contain only synthetic case facts.
- All data is synthetic. If real patient, provider, or payer data is ever found in a trace, the trace is deleted and the source of the data is fixed.
- Text from retrieved policy or clinical passages is untrusted data. It never appears in trace fields that drive behavior.

## Timestamps and deterministic tests

- `sequence_number` alone defines event order.
- Durations come from a monotonic clock, so system clock changes cannot produce negative or wrong values.
- Exact-match trace tests compare every field except `timestamp` and `elapsed_ms`. The runtime should accept an injected clock so the fake-model tests can also fix these values.

## How evaluation uses these fields

| Metric | Calculated from |
|---|---|
| Joint accuracy, selective accuracy, resolution coverage | The final disposition and root cause in the terminal event, compared with the gold label |
| Citation precision | `citation_ids` in `DECISION_PROPOSED`, compared with gold source IDs and the frozen snapshot |
| Consistency | Terminal decisions across three runs that share `experiment_id` and have `repeat_index` 1, 2, and 3 |
| Model, tool, and total latency | `elapsed_ms` on `MODEL_RESPONDED`, `TOOL_RETURNED`, and terminal events |
| Tokens and cost | Summed `input_tokens` and `output_tokens`, multiplied by a versioned price table in `evaluation/`. Runs with `null` usage are reported separately and are never counted as zero |
| Budget and stop analysis | `terminal_reason`, `manual_review_reasons`, and `counters` |
| Repair rate | Number of `MODEL_OUTPUT_REJECTED` and `REPAIR_ATTEMPTED` events |
| Tool failure and fault injection | `status` and `error_code` on `TOOL_RETURNED` |
| Safety checks | The `VALIDATION_RETURNED` results, and `approval_state` equal to `PENDING_REVIEW` |
| Reproducibility | `config_sha256`, `code_commit`, `model_id`, and `knowledge_snapshot_id` match across repeat runs |

## Examples

- [`evaluation/examples/trace_success.jsonl`](../../evaluation/examples/trace_success.jsonl): a medical-necessity case that is resolved and passes validation.
- [`evaluation/examples/trace_budget_exhausted.jsonl`](../../evaluation/examples/trace_budget_exhausted.jsonl): an ambiguous CO-16 case. The model output is rejected once and repaired, then the run reaches the model-call limit and ends in manual review.

Model names, tool names, passage IDs, and the values inside `AgentDecision` and `ValidationResult` in these examples are placeholders. They will be updated when #3 and #6 are frozen.

## Open points for reviewers

- **Hriday:** Can the runtime emit every required field? Is it acceptable to remove `estimated_cost`?
- **Silas:** Do the `TOOL_RETURNED` payload fields match the `ToolResult` contract in #3?
- **Neha:** Do the `VALIDATION_RETURNED` payload and the terminal reason codes match the `ValidationResult` contract in #6?

## Consequences

- Hriday implements `TraceEvent` and `AgentRunConfig` from this document.
- Evaluation code depends only on the fields listed here.
- After the held-out freeze, any field change requires a new `trace_version` and a new version of this document.
