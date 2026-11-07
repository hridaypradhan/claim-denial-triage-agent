# 0002: Trace events and agent run configuration

- **Status:** Proposed
- **Version:** 0.2 (`trace_version` and `config_version` are both `"0.2"`)
- **Date:** 2026-11-07 (first draft 2026-10-29)
- **Issue:** #7
- **Requirements owner:** Sachin Kothandaraman (evaluation)
- **Runtime producer and required reviewer:** Hriday Pradhan
- **Input requested from:** Silas Sangmin (tool events), Neha Kashyap (validation events)
- **Related:** shared contracts `0.1` in `src/claim_agent/contracts/schema.py` (#3, #5, #6, all merged), PR #10 (runtime), #11 (trace adapter), #12 (evaluation runner)

This record is numbered `0002` because decision `0001` is the retrieval method in PR #4.

## Context

The evaluation must be able to do three things with every agent run:

- run it again with the same settings,
- measure its cost, speed, and consistency,
- explain why it stopped.

To do that, the runtime must write a fixed trace format (`TraceEvent`) and record the settings it ran with (`AgentRunConfig`). This document answers the ten decisions listed in #7. The runtime's in-memory trace from PR #10 is the producer; Hriday's adapter in #11 converts its entries into this format.

Payloads owned by the shared contracts (`RouteDecision`, `ToolResult`, `AgentDecision`, `ValidationResult`, all at contract version `0.1`) are referenced here, not redefined (see [Nested contracts and versioning](#nested-contracts-and-versioning)).

## Decision summary

| # | Question in #7 | Decision |
|---|---|---|
| 1 | Which fields are required on every event? | Eight fields are always required. Others are required only for certain event types (see [TraceEvent fields](#traceevent-fields)). |
| 2 | Is append-only JSON Lines required? | Yes. Each run writes one UTF-8 `.jsonl` file with one event per line, and lines are never edited. |
| 3 | Store full prompts and evidence, or reference them? | Reference them. Store the prompt version and a hash of the rendered prompt, and evidence passage IDs. |
| 4 | What must be excluded or redacted? | A recursive, default-deny allowlist applies to every payload. Free text is replaced by `[REDACTED]` plus a hash (see [Redaction and data safety](#redaction-and-data-safety)). |
| 5 | Timestamps, or sequence numbers only? | Both are written. Ordering and deterministic tests use `sequence_number` only. |
| 6 | Who calculates cost? | The evaluation harness, from token counts and a versioned price table. The runtime records tokens only. |
| 7 | Which terminal and manual-review reason codes? | A fixed list of terminal reasons, plus evidence reasons recorded alongside them. PR #10's internal reasons map one-to-one (see [Terminal reasons](#terminal-reasons)). |
| 8 | Which model parameters are frozen for repeat runs? | Every field in `AgentRunConfig`, including the code commit, seeds, request timeouts, allowed tools, and retry limit. |
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
| `trace_version` | string | `"0.2"` for this version |
| `run_id` | string | Unique for each run |
| `case_id` | string | The evaluation case identifier (see below) |
| `sequence_number` | int | Ordering key, starting at 1 |
| `event_type` | enum | See [Event types](#event-types) |
| `component` | enum | `AGENT_RUNTIME`, `MODEL_CLIENT`, `TOOL_DISPATCHER`, or `SAFETY_VALIDATOR` |
| `timestamp` | string | UTC ISO 8601. Recorded for people to read; not used for ordering |
| `payload` | object | Event-specific content. `{}` if there is none |

`case_id` identifies one evaluation case: the unit that has one gold label. The router produces one `RouteDecision` per service line, and one agent run handles exactly one `(claim_id, service_line_id)`, so an evaluation case is one service line. `case_id` is written as `<claim_id>-L<service_line_id>`, for example `CLM-2026-005097-L1`. The exact `claim_id` and `service_line_id` are also recorded in the `RUN_STARTED` route, so evaluation never has to parse `case_id`.

### Required for some event types

| Field | Required on | Notes |
|---|---|---|
| `request_id` | `MODEL_REQUESTED`, `MODEL_RESPONDED`, `MODEL_OUTPUT_REJECTED`, `MODEL_FAILED`, `REPAIR_ATTEMPTED`, `TOOL_REQUESTED`, `TOOL_RETURNED`, `TOOL_FAILED` | Pairs a request with its outcome. See [Request IDs during repair](#request-ids-during-repair) and [Tool retries](#tool-retries) |
| `model_id` | `MODEL_REQUESTED`, `MODEL_RESPONDED` | Exact pinned model version |
| `prompt_version` | `MODEL_REQUESTED` | Frozen prompt version |
| `elapsed_ms` | `MODEL_RESPONDED`, `MODEL_FAILED`, `TOOL_RETURNED`, `TOOL_FAILED`, terminal events | Model latency, tool latency, or total run time |
| `input_tokens`, `output_tokens` | `MODEL_RESPONDED` | Integer, or `null` with a `usage_status` |
| `usage_status` | `MODEL_RESPONDED` | `REPORTED`, `NOT_REPORTED_BY_PROVIDER`, or `NOT_APPLICABLE` (scripted fake model) |
| `terminal_reason` | `RUN_COMPLETED`, `MANUAL_REVIEW` | See [Terminal reasons](#terminal-reasons) |

Fields that do not apply to an event are omitted, not set to `null`. `null` always means "this applies but the value is unavailable".

### Change from the #7 proposal

`estimated_cost` is removed from `TraceEvent`. Cost is calculated by the evaluation harness (decision 6), so the runtime never writes it. Removing the field prevents two different cost numbers from existing for the same run.

## Event types

| Event type | Component | Payload must include |
|---|---|---|
| `RUN_STARTED` | `AGENT_RUNTIME` | The full `AgentRunConfig`, `config_sha256` (see [how it is calculated](#how-config_sha256-is-calculated)), `route`, and an optional `evaluation_context` with `experiment_id` and `repeat_index`. `route` holds the coded fields of the `RouteDecision` that started the run: `contract_version`, `claim_id`, `service_line_id`, `outcome`, `reason_code`, and `authorized_tools` (`routing_reason` is free text and is left out) |
| `MODEL_REQUESTED` | `MODEL_CLIENT` | `call_index`, `prompt_sha256` (SHA-256 of the UTF-8 rendered prompt), the `passage_ids` included in context, and `repair_attempt` (0 if this is not a repair) |
| `MODEL_RESPONDED` | `MODEL_CLIENT` | `response_type` (`TOOL_CALLS`, `DECISION`, `MANUAL_REVIEW_REQUEST`, or `INVALID`) and the structured response, after [redaction](#redaction-and-data-safety). An `INVALID` response stores `raw_output: "[REDACTED]"` and `raw_output_sha256` |
| `MODEL_OUTPUT_REJECTED` | `AGENT_RUNTIME` | `rejection_code` and `schema_error_paths` (field paths only, never values) |
| `MODEL_FAILED` | `MODEL_CLIENT` | See [Boundary-failure events](#boundary-failure-events) |
| `REPAIR_ATTEMPTED` | `AGENT_RUNTIME` | `repair_index`, `repair_kind` (`OUTPUT_PARSE` or `VALIDATION`), and `rejected_request_id` |
| `TOOL_REQUESTED` | `TOOL_DISPATCHER` | `tool_call_index`, `attempt`, `tool_name`, and redacted `tool_args` |
| `TOOL_RETURNED` | `TOOL_DISPATCHER` | `tool_name`, `attempt`, and the `ToolResult` fields (contract `0.1`): `status`, `error_code`, `retryable`, `source_ids`, and `passage_ids` (the IDs of `evidence_passages`). Never passage text |
| `TOOL_FAILED` | `TOOL_DISPATCHER` | See [Boundary-failure events](#boundary-failure-events) |
| `DECISION_PROPOSED` | `AGENT_RUNTIME` | The full `AgentDecision` (contract `0.1`) after redaction: `contract_version`, `decision_id`, `claim_id`, `service_line_id`, `root_cause`, `recommended_disposition`, `actions`, `criterion_assessments`, `evidence_gaps`, `agent_status`, `manual_review_reasons`, `rationale`, and `citation_ids` |
| `VALIDATION_RETURNED` | `SAFETY_VALIDATOR` | The full `ValidationResult` (contract `0.1`) after redaction: `contract_version`, `decision_id`, `claim_id`, `service_line_id`, `outcome`, `approval_state`, `validation_problems`, `validated_decision` (present only when `outcome` is `ACCEPTED`), and `manual_review_reasons` |
| `VALIDATION_FAILED` | `SAFETY_VALIDATOR` | See [Boundary-failure events](#boundary-failure-events) |
| `RUN_COMPLETED` | `AGENT_RUNTIME` | `final_disposition`, `final_root_cause`, `approval_state` (always `PENDING_REVIEW`), and `counters` |
| `MANUAL_REVIEW` | `AGENT_RUNTIME` | `manual_review_reasons` (one or more), `approval_state` (always `PENDING_REVIEW`), and `counters` |

`counters` records the budgets used: `model_calls`, `tool_calls`, `tool_retries`, `repairs`, and `total_steps`.

Two internal runtime events are converted rather than copied:

- `PHASE_CHANGED` is not written, because every phase change is already implied by the events above.
- `TOOL_RETRY_REQUESTED` is written as a `TOOL_REQUESTED` event for the retry (see [Tool retries](#tool-retries)).

The runtime currently returns one action per model response, so a `MODEL_RESPONDED` event holds at most one tool call. `tool_calls` is still a list, so batched tool calls can be added later without changing the trace format.

### Request IDs during repair

Every model call gets its own new `request_id`, including a repair call. A repair never reuses the ID of the call it is fixing.

| Event | `request_id` means | Link to the rejected call |
|---|---|---|
| `MODEL_OUTPUT_REJECTED` | The call whose output was rejected | — |
| `REPAIR_ATTEMPTED` | The **new** repair call that is about to be made | `payload.rejected_request_id` holds the ID of the rejected call |
| `MODEL_REQUESTED` and `MODEL_RESPONDED` for the repair | The new repair call (same ID as `REPAIR_ATTEMPTED`) | `payload.repair_attempt` is 1 or higher on `MODEL_REQUESTED` |

For example, if call `REQ-M2` is rejected, the events are `MODEL_OUTPUT_REJECTED` with `request_id: "REQ-M2"`, then `REPAIR_ATTEMPTED` with `request_id: "REQ-M3"` and `rejected_request_id: "REQ-M2"`, then `MODEL_REQUESTED` and `MODEL_RESPONDED` with `request_id: "REQ-M3"`.

**What a repair may return** follows PR #10:

- An `OUTPUT_PARSE` repair fixes malformed output. It may return any permitted action, including a tool call, because the original call never produced a usable action.
- A `VALIDATION` repair fixes a decision the validator rejected. It must return a proposed decision or a manual-review request. Anything else ends the run with `INVALID_REPAIR_ACTION`.

### Tool retries

A retry repeats one tool call after a retryable failure. It is not a new call by the model.

- The retry has a new `request_id`, the same `tool_call_index`, and `attempt: 2`. Its `TOOL_REQUESTED` payload also has `retry_of_request_id`, which holds the failed attempt's ID. The adapter builds this event from the runtime's `TOOL_RETRY_REQUESTED` entry, whose `previous_request_id` becomes `retry_of_request_id`.
- The retry uses one unit of the tool-call budget and is also counted in `counters.tool_retries`. No model call is made for it.
- At most `max_tool_retries` retries are made per tool call. Only results with `retryable: true` are retried.

## Boundary-failure events

PR #10 records these when a boundary raises an exception instead of returning a result. They are different from a normal failed result. For example, "the validator rejected the decision" is `VALIDATION_RETURNED` with a non-accepted status, while "the validator crashed" is `VALIDATION_FAILED`.

| Event | Emitted by | When | Terminal reason |
|---|---|---|---|
| `MODEL_FAILED` | `MODEL_CLIENT` | The model or repair-model call raises | `MODEL_REQUEST_TIMEOUT` when the request timed out, otherwise `MODEL_BOUNDARY_ERROR` |
| `TOOL_FAILED` | `TOOL_DISPATCHER` | The dispatcher raises instead of returning a `ToolResult` | `TOOL_BOUNDARY_ERROR` |
| `VALIDATION_FAILED` | `SAFETY_VALIDATOR` | The deterministic validator raises | `VALIDATOR_BOUNDARY_ERROR` |

Each failure event carries:

| Field | Where | Notes |
|---|---|---|
| `request_id` | Envelope | Required on `MODEL_FAILED` and `TOOL_FAILED`. `VALIDATION_FAILED` uses `payload.decision_id` instead |
| `elapsed_ms` | Envelope | Time from the request to the exception, for `MODEL_FAILED` and `TOOL_FAILED` |
| `failure_class` | Payload | `BOUNDARY_EXCEPTION`, or `REQUEST_TIMEOUT` when the request-level timeout fired |
| `error_type` | Payload | The exception class name only, for example `KnowledgeStoreError`. The exception message is never written because it can contain data |
| `retryable` | Payload | Always `false` in this version. A raised exception is never retried |
| `repair_attempt` | Payload, `MODEL_FAILED` only | 0, or the repair attempt number if a repair call failed |
| `tool_name`, `attempt` | Payload, `TOOL_FAILED` only | The tool and attempt that failed |

The runtime already records `request_id` on `TOOL_FAILED`. Its internal `VALIDATION_FAILED` entry does not record `decision_id`, so the adapter in #11 adds it from the preceding `DECISION_PROPOSED` event.

A failure event is always followed directly by the terminal `MANUAL_REVIEW` event. The budget already used by the failed call stays counted.

## Terminal reasons

`terminal_reason` says why the run stopped. `MANUAL_REVIEW` events also list every reason that applied in `manual_review_reasons`. The first item equals `terminal_reason`, followed by any [evidence reasons](#evidence-reasons).

| Code | Terminal event | Meaning |
|---|---|---|
| `COMPLETED_VALIDATED` | `RUN_COMPLETED` | A decision passed every deterministic validation check |
| `VALIDATOR_REQUIRED_REVIEW` | `MANUAL_REVIEW` | The validator returned a manual-review result |
| `MODEL_REQUESTED_REVIEW` | `MANUAL_REVIEW` | The model asked for manual review, in a normal or a repair call |
| `MODEL_CALL_BUDGET_EXHAUSTED` | `MANUAL_REVIEW` | The `max_model_calls` limit was reached |
| `TOOL_CALL_BUDGET_EXHAUSTED` | `MANUAL_REVIEW` | The `max_tool_calls` limit was reached |
| `REPAIR_BUDGET_EXHAUSTED` | `MANUAL_REVIEW` | The `max_repair_attempts` limit was reached |
| `STEP_BUDGET_EXHAUSTED` | `MANUAL_REVIEW` | The `max_total_steps` limit was reached |
| `RUN_TIMEOUT` | `MANUAL_REVIEW` | The `max_runtime_seconds` limit was reached for the whole run |
| `REPAIR_OUTPUT_INVALID` | `MANUAL_REVIEW` | A repair call still produced unparseable output |
| `INVALID_REPAIR_ACTION` | `MANUAL_REVIEW` | A validation repair returned something other than a decision or manual-review request |
| `TOOL_NOT_ALLOWED` | `MANUAL_REVIEW` | The model requested a tool that is not in `allowed_tools` |
| `TOOL_UNAVAILABLE` | `MANUAL_REVIEW` | A tool returned a retryable failure, every allowed retry also failed, and the runtime stopped |
| `MODEL_REQUEST_TIMEOUT` | `MANUAL_REVIEW` | A model or repair-model request hit `model_request_timeout_seconds`. `payload.during_repair` says which |
| `MODEL_BOUNDARY_ERROR` | `MANUAL_REVIEW` | `MODEL_FAILED` was recorded for any reason other than a request timeout |
| `TOOL_BOUNDARY_ERROR` | `MANUAL_REVIEW` | `TOOL_FAILED` was recorded |
| `VALIDATOR_BOUNDARY_ERROR` | `MANUAL_REVIEW` | `VALIDATION_FAILED` was recorded |
| `RUNTIME_ERROR` | `MANUAL_REVIEW` | Any other unexpected error. The run still ends in a safe state |

`RUN_TIMEOUT` replaces the earlier `TIMEOUT` so it cannot be confused with a tool's `TIMEOUT` error code. `RUN_TIMEOUT` is the whole-run limit; `MODEL_REQUEST_TIMEOUT` is one model request. `TOOL_UNAVAILABLE` covers only returned retryable failures after retries. A raised dispatcher exception is `TOOL_BOUNDARY_ERROR`, and a single non-retryable error result does not stop the run by itself: it goes back to the model as an observation.

### Mapping from the runtime

Every internal terminal reason in the merged runtime (PR #10) maps to one trace reason:

| Runtime internal reason | Trace `terminal_reason` |
|---|---|
| `decision_accepted` | `COMPLETED_VALIDATED` |
| `decision_requires_manual_review` | `VALIDATOR_REQUIRED_REVIEW` |
| `model_requested_manual_review`, `repair_requested_manual_review` | `MODEL_REQUESTED_REVIEW`, with `payload.during_repair` set to `false` or `true` |
| `model_timeout`, `repair_model_timeout` | `MODEL_REQUEST_TIMEOUT`, with `payload.during_repair` set to `false` or `true` |
| `model_budget_exhausted`, `tool_budget_exhausted`, `repair_budget_exhausted` | Chosen from the exhausted budget's `budget_name`: `model_call` → `MODEL_CALL_BUDGET_EXHAUSTED`, `tool_call` → `TOOL_CALL_BUDGET_EXHAUSTED`, `repair_attempt` → `REPAIR_BUDGET_EXHAUSTED`, `total_step` → `STEP_BUDGET_EXHAUSTED` |
| `runtime_timeout` | `RUN_TIMEOUT` |
| `repair_output_invalid` | `REPAIR_OUTPUT_INVALID` |
| `invalid_validation_repair_action` | `INVALID_REPAIR_ACTION` |
| `tool_not_allowed` | `TOOL_NOT_ALLOWED` |
| `model_boundary_error` | `MODEL_BOUNDARY_ERROR` |
| `tool_boundary_error` | `TOOL_BOUNDARY_ERROR` |
| `validation_boundary_error` | `VALIDATOR_BOUNDARY_ERROR` |
| Any reason not in this table | `RUNTIME_ERROR`, and the adapter's tests fail so the new reason gets a mapping |

The budget rows use `budget_name` because PR #10 reports, for example, `model_budget_exhausted` even when the total-step budget was the one that ran out.

### Evidence reasons

These say what evidence was missing or which safety rule stopped the decision. They are added to `manual_review_reasons` after the terminal reason, from two sources:

- **Validator escalation reasons**, copied exactly as `ValidationResult.manual_review_reasons` reports them. These are the `ESCALATION_*` constants in `src/claim_agent/contracts/schema.py`, for example `SAFETY_ESCALATION_DEADLINE_EXPIRED` or `SAFETY_ESCALATION_MISSING_EVIDENCE`.
- **Tool errors** the model saw during the run, mapped to the codes below.

| Code | Meaning |
|---|---|
| `MISSING_EVIDENCE` | Evidence the decision depends on was not found |
| `CONFLICTING_EVIDENCE` | Sources disagreed and the conflict could not be resolved |
| `POLICY_NOT_APPLICABLE` | No single policy applied on the date of service |
| `DEADLINE_EXPIRED` | The filing deadline for the recoverable action had passed |
| `MALFORMED_TOOL_REQUEST` | The model sent invalid tool arguments |

Tool error codes (the `ErrorCode` enum in contracts `0.1`) map to evidence reasons as follows:

| Tool `error_code` | Evidence or terminal reason |
|---|---|
| `NO_MATCH` from `retrieve_medical_policy` | `POLICY_NOT_APPLICABLE` |
| `NO_MATCH` from any other tool | `MISSING_EVIDENCE` |
| `AMBIGUOUS_POLICY` | `POLICY_NOT_APPLICABLE`. The `TOOL_RETURNED` event keeps the exact error code for analysis |
| `DOCUMENT_UNAVAILABLE` | `MISSING_EVIDENCE` |
| `MALFORMED_REQUEST` | `MALFORMED_TOOL_REQUEST`. Not retryable; the error goes back to the model |
| `TRANSIENT_FAILURE` or tool `TIMEOUT`, after all retries fail | Terminal reason `TOOL_UNAVAILABLE` |

## Passage and source identifiers

These follow the merged `ToolResult` and `EvidencePassage` contracts (`0.1`) and decision 0001.

- **`passage_ids`** hold passage IDs in the form `<source_id>@v<version>#<passage>`, for example `POL-BCBSAZ-MRI-BRAIN@v1#P3`.
- **`source_ids`** hold versioned document IDs in the form `<source_id>@v<version>`, for example `POL-BCBSAZ-MRI-BRAIN@v1`.
- On `TOOL_RETURNED`, `passage_ids` are the `passage_id`s of `ToolResult.evidence_passages`, in the order the tool returned them, which is the ranked order for policy retrieval. When passages are returned, `source_ids` are the distinct documents of those passages in order of first appearance, as `ToolResult` already enforces.
- A tool that returns no passages, such as `lookup_denial_code` or `get_payer_profile`, may still return `source_ids`, with `passage_ids` empty. A failed tool call returns both lists empty.
- `citation_ids` in decisions and the `passage_ids` in `MODEL_REQUESTED` are always passage IDs.
- Citation precision compares passage IDs, never source IDs.

## AgentRunConfig

All of these fields are frozen before the held-out evaluation. A repeat run must use exactly the same values. The whole config is written into the `RUN_STARTED` payload.

| Field | Type | Notes |
|---|---|---|
| `config_version` | string | Version of this config format |
| `code_commit` | string | Git commit of the code that ran |
| `model_provider` | string | |
| `model_id` | string | Exact dated model version, never an alias |
| `model_parameters` | object | Every sampling parameter: at least `temperature`, `top_p`, and `max_output_tokens`. A parameter that is not sent is `null` |
| `prompt_version` | string | |
| `runtime_seed` | int | Seed for everything the runtime controls: the scripted fake model and any runtime randomness |
| `provider_seed` | int or `null` | Seed sent to the model provider. `null` when the provider does not support one |
| `provider_seed_supported` | bool | Whether the provider accepted `provider_seed`. When `false`, repeat runs may differ, which is why consistency is measured across three runs |
| `max_model_calls` | int | Repair calls count toward this |
| `max_tool_calls` | int | Retries count toward this |
| `max_repair_attempts` | int | Same name as PR #10 |
| `max_total_steps` | int | Model calls plus tool calls |
| `max_tool_retries` | int | Maximum retries per tool call (see [Tool retries](#tool-retries)) |
| `max_runtime_seconds` | number | Total run time limit. Same name and type as PR #10 |
| `model_request_timeout_seconds` | number | Limit for one model request |
| `tool_request_timeout_seconds` | number | Limit for one tool request |
| `allowed_tools` | list | Each entry has `tool_name` and `tool_version`, sorted by `tool_name`. This freezes the tool allowlist |
| `knowledge_snapshot_id` | string | Frozen knowledge corpus version, for example `knowledge-v0` |

Fields typed "number" are always written as floats (`30.0`, not `30`), and int fields are always written as integers. This keeps the hash below identical across runs.

### How `config_sha256` is calculated

The runtime and the evaluation harness must produce the same hash for the same config. Both use this exact method:

1. Take the `AgentRunConfig` object with all of its fields, including fields whose value is `null`.
2. Serialize it as JSON with keys sorted alphabetically at every level, no spaces (`,` between items and `:` between key and value), and non-ASCII characters written as-is, not escaped. Floats are written as Python's `json` module writes them.
3. Encode the result as UTF-8 bytes with no trailing newline.
4. Hash the bytes with SHA-256 and write the result as 64 lowercase hexadecimal characters.

In Python this is:

```python
hashlib.sha256(
    json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
).hexdigest()
```

**Shared test vector:** [`evaluation/examples/config_hash_vector.json`](../../evaluation/examples/config_hash_vector.json) holds one fixed config, its exact canonical JSON, and its expected hash. The runtime's tests and the evaluation harness's tests must both reproduce that hash.

## Redaction and data safety

### Rules

- **Never written:** API keys, tokens, authorization headers, environment variables, provider endpoint URLs containing credentials, local file paths or usernames, and exception messages.
- **Referenced, not copied:** the rendered prompt (`prompt_sha256`), evidence text (`passage_ids` and `source_ids`), and the claim record (`case_id`).
- **Default deny:** every payload is filtered recursively before it is written, including the full model response, `AgentDecision`, and `ValidationResult`. A value is kept only if its key is in one of the allowed groups below.

### Allowed groups

| Group | Keys | Kept because |
|---|---|---|
| Claim fields | `case_id`, `claim_id`, `service_line_id`, `payer`, `payer_id`, `date_of_service`, `service_date`, `cpt`, `procedure_code`, `modifiers`, `icd10`, `group_code`, `carc`, `rarc` | Needed to know which claim, policy, and line a step was about. All values are synthetic |
| Identifiers | `knowledge_snapshot_id`, `document_id`, `policy_id`, `source_ids`, `passage_ids`, `citation_ids`, `criterion_id`, `decision_id`, `request_id`, `rejected_request_id`, `retry_of_request_id`, `run_id`, `experiment_id`, `authorized_tools` | Point to frozen sources, tools, or other events; contain no free text |
| Run configuration | Every `AgentRunConfig` field | Fixed settings; contain no case data |
| Controlled values | Any field whose value comes from a fixed list: event types, statuses, `outcome`, `reason_code`, error codes, validation problem `code`, reason codes (including `manual_review_reasons` and `evidence_gaps`), `root_cause`, `recommended_disposition`, `final_disposition`, `final_root_cause`, `agent_status`, criterion `status`, `response_type`, `rejection_code`, `schema_error_paths`, problem `field_path`, `repair_kind`, `failure_class`, `tool_name`, `error_type`, `approval_state`, and `contract_version` | Values are codes or field paths, not text |
| Numbers, booleans, dates, and hashes | Counters, indexes, token counts, `elapsed_ms`, `retryable`, `is_repairable`, `during_repair`, and every field ending in `_sha256` | Contain no free text |
| Structure | `config`, `payload`, `route`, `decision`, `criterion_assessments`, `validation_problems`, `validated_decision`, `tool_calls`, `tool_args`, `counters`, `evaluation_context` | Containers whose contents are filtered by the same rules |

### Tool arguments

Every argument the current tools accept is covered:

| Tool | Arguments | Written as |
|---|---|---|
| `retrieve_medical_policy` | `payer`, `procedure_code`, `service_date` | Kept |
| | `query` | `"[REDACTED]"` plus `query_sha256` |
| `get_encounter_note` | `claim_id`, `document_id` | Kept |
| `lookup_denial_code` | `carc`, `rarc`, `group_code` | Kept |
| `get_payer_profile` | `payer_id` | Kept |

The runtime's internal `argument_count` field is not written, because it adds nothing the arguments don't already show.

### Free text

Any other string, such as `rationale`, a criterion `explanation`, a validator problem `message`, a `routing_reason`, a search `query`, or invalid raw model output, is written as `"[REDACTED]"`. A sibling field `<name>_sha256` holds the SHA-256 of the original UTF-8 text.

A list of free-text strings, such as `actions`, keeps its length: each item becomes `"[REDACTED]"`, and `<name>_sha256` holds the SHA-256 of the original list serialized with the canonical JSON method used for `config_sha256`.

The hash lets evaluation detect identical queries or rationales across calls and repeat runs without storing the text. For example, a search query is written as `"query": "[REDACTED]"` with `"query_sha256": "..."`.

### Enforcement

- The adapter in #11 implements the groups above as an explicit list of keys in code, and applies it to every payload before writing a line. Keys that are not allowed are kept with the value `"[REDACTED]"`, so the trace still shows the field existed.
- The evaluation harness checks every trace before scoring. A trace with a non-allowed value that is not `"[REDACTED]"` fails the check and is reported as a data-safety defect.
- Adding a key to an allowed group requires a new version of this document.
- All data is synthetic. If real patient, provider, or payer data is ever found in a trace, the trace is deleted and the source of the data is fixed.
- Text from retrieved policy or clinical passages is untrusted data. It is never written to a trace and never appears in a field that drives behavior.

## Timestamps and deterministic tests

- `sequence_number` alone defines event order.
- Durations come from a monotonic clock, so system clock changes cannot produce negative or wrong values.
- Exact-match trace tests compare every field except `timestamp` and `elapsed_ms`. The runtime should accept an injected clock so the fake-model tests can also fix these values.

## Nested contracts and versioning

Some payloads belong to the shared contracts in `src/claim_agent/contracts/schema.py`. Each carries that contract's own `contract_version`.

| Payload | Owner contract | Status |
|---|---|---|
| `RUN_STARTED.route` | `RouteDecision` (#5) | Merged, `0.1` |
| `TOOL_RETURNED` fields | `ToolResult` and `EvidencePassage` (#3) | Merged, `0.1` |
| `DECISION_PROPOSED` | `AgentDecision` (#6) | Merged, `0.1` |
| `VALIDATION_RETURNED` | `ValidationResult` (#6) | Merged, `0.1` |

Versioning rules:

- **New `trace_version`:** any change to envelope fields, event types, terminal or evidence reasons, redaction rules, or the meaning of `case_id`.
- **Nested contract version only:** a change inside a nested payload that keeps the envelope and the fields listed in this document. The nested payload's `contract_version` changes, and `trace_version` does not.
- After the held-out freeze, any change of either kind needs a new version of this document.

## How evaluation uses these fields

| Metric | Calculated from |
|---|---|
| Joint accuracy, selective accuracy, resolution coverage | The final disposition and root cause in the terminal event, compared with the gold label |
| Citation precision | Passage IDs in `citation_ids`, compared with gold passage IDs and checked against the frozen snapshot |
| Consistency | Terminal decisions across three runs that share `experiment_id` and have `repeat_index` 1, 2, and 3 |
| Model, tool, and total latency | `elapsed_ms` on `MODEL_RESPONDED`, `TOOL_RETURNED`, and terminal events |
| Tokens and cost | Summed `input_tokens` and `output_tokens`, multiplied by a versioned price table in `evaluation/`. Runs with `null` usage are reported separately and are never counted as zero |
| Budget and stop analysis | `terminal_reason`, `manual_review_reasons`, and `counters` |
| Repair rate | `MODEL_OUTPUT_REJECTED` and `REPAIR_ATTEMPTED` events, split by `repair_kind` |
| Tool failures and fault injection | `status`, `error_code`, `retryable`, and `attempt` on `TOOL_RETURNED`, and `TOOL_FAILED` events |
| Boundary failures | `MODEL_FAILED`, `TOOL_FAILED`, and `VALIDATION_FAILED` events, with `failure_class` and `error_type` |
| Repeated identical queries | `query_sha256` on tool requests |
| Safety checks | `VALIDATION_RETURNED` results, `approval_state` equal to `PENDING_REVIEW`, and the redaction check |
| Reproducibility | `config_sha256`, `code_commit`, `model_id`, and `knowledge_snapshot_id` match across repeat runs |

## Examples

| File | Shows |
|---|---|
All three runs are for CLM-2026-005097 line 1 (CPT 70553), which the router sends to the agent with reason `EVIDENCE_EVALUATION_REQUIRED`. Each model response requests at most one tool, matching the current runtime.

| File | Shows |
|---|---|
| [`trace_success.jsonl`](../../evaluation/examples/trace_success.jsonl) | Three model turns each request one tool: the policy, then the clinical note, then the neurology note. The fourth turn proposes an appeal with criteria A, B, and C `SUPPORTED` and D `NOT_MET`, so `A AND B AND (C OR D)` holds. The validator returns `ACCEPTED` |
| [`trace_budget_exhausted.jsonl`](../../evaluation/examples/trace_budget_exhausted.jsonl) | The model asks for a document that does not exist (`DOCUMENT_UNAVAILABLE`), its next output is rejected, an `OUTPUT_PARSE` repair requests the policy, and the run reaches its model-call limit. `manual_review_reasons` adds `MISSING_EVIDENCE` |
| [`trace_tool_boundary_failure.jsonl`](../../evaluation/examples/trace_tool_boundary_failure.jsonl) | The dispatcher raises during policy retrieval, producing `TOOL_FAILED` and a `TOOL_BOUNDARY_ERROR` manual review |
| [`config_hash_vector.json`](../../evaluation/examples/config_hash_vector.json) | The shared `config_sha256` test vector |

Notes on the examples:

- `DECISION_PROPOSED` and `VALIDATION_RETURNED` use the merged `AgentDecision` and `ValidationResult` fields (`0.1`), with free text redacted.
- Passage and source IDs are real IDs from `knowledge-v0`. The ranked passage order is illustrative.
- `config_sha256` values are real hashes of the configs shown. `prompt_sha256` values are real SHA-256 hashes of the placeholder text `EXAMPLE PROMPT <run_id> <request_id>`, because the examples have no rendered prompts.
- `code_commit`, model names, timings, and token counts are placeholders until a real run exists.

## Open points for reviewers

Final confirmations needed before this becomes `Accepted`:

- **Hriday:** The adapter can emit the revised fields, including `route`, `MODEL_REQUEST_TIMEOUT`, and the `TOOL_RETRY_REQUESTED` conversion.
- **Silas:** The `TOOL_RETURNED`, retry, and error-mapping fields match the merged `ToolResult`, and the tool-argument table is complete.
- **Neha:** The `DECISION_PROPOSED` and `VALIDATION_RETURNED` payloads, and copying the validator's `SAFETY_ESCALATION_*` reasons into `manual_review_reasons`, match the merged contracts.

## Consequences

- Hriday implements the adapter in #11 from this document.
- Evaluation code in #12 depends only on the fields listed here.
- This record stays `Proposed` until the reviewers above confirm it. It then becomes `Accepted`, and the PR closes #7.
