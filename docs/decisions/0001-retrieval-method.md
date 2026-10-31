# 0001: Retrieval method and knowledge format

- **Status:** Proposed
- **Owner:** Silas Sangmin
- **Reviewers:** Hriday Pradhan (required), Neha Kashyap (citation contract), Sachin Kothandaraman (provenance metrics)
- **Affected contracts:** `ToolResult`, `EvidencePassage`

## Context

The agent must cite policy and encounter passages that deterministic validation can resolve exactly. The milestone requires policy retrieval to filter by payer, procedure, and service date before ranking, and forbids substituting another payer's policy or a policy from a different effective period. The corpus is small (tens of documents), frozen for evaluation, and must work offline.

## Options considered

1. **Embedding search over all passages.** Good recall on paraphrased queries, but adds a model dependency, makes ranking harder to reproduce, and can surface a passage from the wrong payer or date unless filtering is still applied first.
2. **Metadata filter, then lexical ranking (BM25 or keyword overlap).** Deterministic, dependency-free, and easy to explain in an audit. Weaker on paraphrase, which matters little for a small curated corpus.
3. **Metadata filter only, returning every passage of the matched policy.** Simplest, but returns long context and gives the agent no signal about which passage answers its query.

## Decision

Use option 2.

1. **Hard filter.** `retrieve_medical_policy` first selects manifest entries where `payer` equals the claim payer, `procedure_codes` contains the service-line code, and `effective_from <= service_date` and (`effective_to` is null or `service_date <= effective_to`).
2. **No fallback.** If the filter matches nothing, the tool returns status `NO_MATCH`. It never relaxes payer, procedure, or date. If it matches more than one policy version, it returns status `AMBIGUOUS_POLICY` rather than choosing one. Both lead to manual review downstream.
3. **Ranking.** Passages inside the matched policy are ranked with BM25 (`k1 = 1.5`, `b = 0.75`) against the agent's query. Text is lowercased and split on non-alphanumeric characters; no stemming or stop-word removal. Document frequencies are computed over the passages of the matched policy only. Ties are broken by the passage's order in the source file. This is implementable with the standard library and gives identical rankings across runs.
4. **Criteria.** The policy's `criteria` list and the formula for the claim's procedure code (from `criteria_formulas`) are always returned with the result, so the agent sees every applicable criterion even if ranking misses one. A policy that lists a procedure code without a formula for it is a corpus defect and returns `NO_MATCH`.
5. **Encounter notes and eligibility records** are fetched by claim ID and document ID, not searched. A requested document that is not in the manifest returns `DOCUMENT_UNAVAILABLE`.

## Knowledge format

- `knowledge/manifest.json` lists every source with its type, version, path, payer and procedure codes or claim ID, effective dates, and origin (URL, access date, terms-of-use status). Tools load sources only through the manifest.
- Each source file holds numbered passages. Passage IDs have the form `<source_id>@v<version>#<passage>`, for example `POL-BCBSAZ-MRI-BRAIN@v1#P3`.
- Policies store one criteria formula per procedure code in `criteria_formulas`, because requirements can differ by code (for example, a contrast indication applies only to CPT 70553).
- `tests/tools/test_knowledge_corpus.py` checks manifest paths, ID and version consistency, passage-ID uniqueness, criterion references, and formula coverage.
- A passage ID is never reused for different text. Changing a passage creates a new source version and new IDs; the old version stays in the snapshot so earlier traces still resolve.
- Real payer text is added only when `origin.terms_status` is `permitted`. Otherwise sources are synthetic and marked `synthetic: true`.

## Consequences

- Citation validation can resolve every ID with a dictionary lookup over the frozen snapshot.
- Policy-matching errors become measurable: a wrong-payer or wrong-date passage cannot be returned by design, so any occurrence is a tool defect.
- Paraphrased queries may rank the right passage lower. Returning the full criteria list with every result limits the impact; retrieval relevance will be measured on the development set.
- The status names `NO_MATCH`, `AMBIGUOUS_POLICY`, and `DOCUMENT_UNAVAILABLE` are proposals for the `ToolResult` error-code enum and need Neha's approval in contract v0.1.
