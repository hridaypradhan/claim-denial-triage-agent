# Evidence retrieval and citation coverage

This maps each evidence failure mode in #65 to the automated tests that cover
it. End-to-end tests run the real `ToolDispatcher`, and the
`ClaimBoundToolDispatcher` the runtime uses, against the committed knowledge
corpus. They are in [`tests/tools/test_evidence_coverage.py`](../tests/tools/test_evidence_coverage.py).
Unit tests elsewhere cover each boundary on its own.

Run everything with:

```bash
python -m unittest discover -s tests/tools -p "test*.py"
python -m unittest discover -s tests/agent -p "test*.py"
```

| Failure mode | Expected behavior | End-to-end test (corpus case) | Unit tests |
|---|---|---|---|
| Exact match | The policy for the claim's payer, procedure, and date, and the claim's own notes, are returned with citable passage IDs | `ExactMatchTest` (710101) | `test_policy_retrieval`: `test_matching_claim_returns_the_policy`; `test_held_out_policies` |
| No match | An explicit `NO_MATCH` with no sources or passages, never a fallback | `NoMatchTest` (710116 payer, 710109 code, 710324 date, 710307 auth) | `test_policy_retrieval`: `test_filters_never_fall_back`; `test_tool_dispatcher`: `test_no_match_is_an_explicit_error` |
| Incompatible policy version | The service date selects the version, and each version keeps its own formula | `PolicyVersionTest` (Aetna knee MRI, 2026-06-30 / 2026-07-01) | `test_policy_retrieval`: `test_effective_dates_are_inclusive`, `test_version_is_chosen_by_service_date`; overlapping versions are `AMBIGUOUS_POLICY` |
| Unavailable document | A listed but absent document returns `DOCUMENT_UNAVAILABLE` and adds no evidence | `UnavailableEvidenceTest` (710313) | `test_encounter_notes`: `test_listed_but_missing_file_is_unavailable`; `test_run_evidence`: `test_failed_observation_does_not_add_evidence` |
| Conflicting evidence | Both documents are returned unchanged; retrieval never picks a side | `ConflictingEvidenceTest` (710105, 710119, 710321) | `test_held_out_batch_a` and `test_held_out_batch_c`: each claim has exactly its documents |
| Timeout and tool error | Errors keep their code and retryability. A retryable failure is retried once, and an exhausted budget or a dispatcher exception stops for manual review | (local tools don't time out; the runtime boundary is tested) | `test_tool_result_adapter`: `test_failed_result_preserves_error_and_retryability`; `test_tool_execution`: `test_retryable_failure_is_retried_once`, `test_second_retryable_failure_is_not_retried_again`; `test_offline_runtime_resilience`: `test_retry_budget_exhaustion_preserves_first_tool_call`, `test_dispatcher_exception_stops_for_manual_review` |
| Instruction-like evidence | Planted instructions are ordinary passages, reach the model only inside the tool-data block, and don't widen tool authority | `InstructionLikeEvidenceTest` (620110, 710115, 710311, 710322) | `test_evidence_context`: `test_tool_text_remains_inside_data_markers`; `test_repair_context`: `test_instruction_like_rejected_text_remains_inside_data` |
| Cross-claim evidence | Another claim's note is unavailable, and the guard refuses requests naming another claim, payer, procedure, or date | `CrossClaimTest` (decoy `CLM-2026-719925` against 710325) | `test_tool_request_guard`: four rejection tests; `test_encounter_notes`: `test_document_from_another_claim_is_unavailable` |
| Unresolved citation | A decision citing a passage not retrieved in its run, including another claim's, is not accepted | `CitationValidationTest` (005097) | None before #65 for the validator's unresolved-citation check |
| Same-run citation resolution | Citations resolve deterministically from the run's frozen bundle; duplicate, malformed, cross-claim, and unresolved IDs are rejected | `test_evidence_bundle`: `ResolutionTest` (on the 005097 bundle) | `test_evidence_bundle`: `RecorderSafetyTest`, `HardeningTest` (immutability, bound provenance, atomic recording) |
| Frozen corpus | Every hash, field, and ID validates, and any change after the freeze fails | `test_snapshot_freeze`: `test_committed_snapshot_matches_its_lock` (#73, frozen in #74) | `test_snapshot_freeze`; `test_knowledge_corpus` |

**Not covered here:** whether a model reasons correctly over this evidence.
That belongs to verification (#60) and the final evaluation (#66).
