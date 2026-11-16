"""Record same-run evidence bundles during an agent run (#59).

``EvidenceBundleRecorder`` wraps the claim-bound tool dispatcher and records
every retrieval call and every returned passage, with provenance taken from
the trusted claim context rather than from model arguments. ``freeze()``
produces the immutable ``EvidenceBundle`` contract defined in
``claim_agent.contracts.evidence_bundle``, which this module re-exports.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from claim_agent.contracts import ToolResult, ToolStatus
from claim_agent.contracts.evidence_bundle import (  # noqa: F401  (re-exported)
    BUNDLE_VERSION,
    ELIGIBILITY_SOURCE_TYPE,
    PASSAGE_ID_PATTERN,
    AvailableSource,
    BundledPassage,
    CitationProblem,
    CitationProblemCode,
    CitationResolutionError,
    ClaimEvidenceScope,
    EvidenceBundle,
    RetrievalRecord,
)


class EvidenceBundleError(ValueError):
    """Raised when recorded evidence would make the bundle inconsistent."""


class EvidenceBundleRecorder:
    """Record a run's retrievals while forwarding them unchanged.

    It implements the same ``dispatch`` interface as the dispatcher it wraps,
    so it can sit between the claim-bound dispatcher and the runtime adapter.
    """

    def __init__(
        self,
        dispatcher: object,
        run_id: str,
        knowledge_snapshot_id: str,
        scope: ClaimEvidenceScope,
        available_sources: Sequence[AvailableSource] = (),
    ) -> None:
        """``available_sources`` lists the claim's documents, usually from
        ``claim_available_sources``; their ``retrieved`` flags are set at freeze."""

        self._dispatcher = dispatcher
        self._available_sources = tuple(available_sources)
        self._run_id = run_id
        self._snapshot_id = knowledge_snapshot_id
        self._scope = scope
        self._retrievals: List[RetrievalRecord] = []
        self._passages: Dict[str, BundledPassage] = {}
        self._policy_formula: Optional[str] = None
        self._frozen: Optional[EvidenceBundle] = None

    def dispatch(self, request_id: str, tool_name: str, tool_arguments: Mapping[str, object]) -> ToolResult:
        if self._frozen is not None:
            raise EvidenceBundleError("The bundle is frozen; no further retrieval may be recorded.")
        result: ToolResult = self._dispatcher.dispatch(
            request_id=request_id, tool_name=tool_name, tool_arguments=tool_arguments
        )
        self._record(request_id, tool_name, result)
        return result

    def _record(self, request_id: str, tool_name: str, result: ToolResult) -> None:
        """Validate one result completely, then record it; a rejected result leaves no trace."""

        if result.request_id != request_id or result.tool_name != tool_name:
            raise EvidenceBundleError("Tool result identity does not match its request.")
        call_index = len(self._retrievals) + 1
        ok = result.status is ToolStatus.OK
        formula = None
        source_snapshot = None
        new_passages: Dict[str, BundledPassage] = {}

        if ok:
            reported_claim = result.data.get("claim_id")
            if reported_claim is not None and reported_claim != self._scope.claim_id:
                raise EvidenceBundleError(f"{tool_name} returned evidence for claim {reported_claim!r}.")
            reported_snapshot = result.data.get("knowledge_snapshot_id")
            if reported_snapshot is not None and reported_snapshot != self._snapshot_id:
                raise EvidenceBundleError(f"{tool_name} returned evidence from snapshot {reported_snapshot!r}.")
            source_snapshot = result.data.get("eligibility_snapshot_id")
            if any(p.source_type == ELIGIBILITY_SOURCE_TYPE for p in result.evidence_passages) and not source_snapshot:
                raise EvidenceBundleError(f"{tool_name} returned eligibility evidence without its snapshot ID.")
            formula = result.data.get("criteria_formula")
            if formula is not None and self._policy_formula is not None and self._policy_formula != formula:
                raise EvidenceBundleError("A run cannot contain conflicting policy formulas.")

            for passage in result.evidence_passages:
                existing = self._passages.get(passage.passage_id) or new_passages.get(passage.passage_id)
                if existing is not None:
                    if existing.passage != passage:
                        raise EvidenceBundleError(
                            f"Passage {passage.passage_id} was returned with conflicting content."
                        )
                    continue  # keep the first retrieval's provenance
                new_passages[passage.passage_id] = BundledPassage(
                    passage=passage,
                    source_type=passage.source_type,
                    document_type=result.data.get("document_type"),
                    claim_id=self._scope.claim_id,
                    payer=self._scope.payer,
                    procedure_code=self._scope.procedure_code,
                    service_date=self._scope.service_date,
                    tool_name=tool_name,
                    request_id=request_id,
                    call_index=call_index,
                )

        # Every check passed: update the recorder state together.
        self._retrievals.append(
            RetrievalRecord(
                call_index=call_index,
                request_id=request_id,
                tool_name=tool_name,
                status=result.status,
                error_code=None if result.error_code is None else result.error_code.value,
                source_ids=tuple(result.source_ids),
                passage_ids=tuple(p.passage_id for p in result.evidence_passages) if ok else (),
                source_snapshot_id=source_snapshot,
            )
        )
        if formula is not None:
            self._policy_formula = formula
        self._passages.update(new_passages)

    def freeze(self) -> EvidenceBundle:
        """Return the run's immutable bundle; later calls return the same bundle."""

        if self._frozen is None:
            retrieved = {(p.passage.source_id, p.passage.version) for p in self._passages.values()}
            available = tuple(
                source.model_copy(update={"retrieved": (source.source_id, source.version) in retrieved})
                for source in self._available_sources
            )
            self._frozen = EvidenceBundle(
                run_id=self._run_id,
                knowledge_snapshot_id=self._snapshot_id,
                scope=self._scope,
                policy_formula=self._policy_formula,
                passages=tuple(self._passages.values()),
                retrievals=tuple(self._retrievals),
                available_sources=available,
            )
        return self._frozen


def claim_available_sources(knowledge_store: object, claim_id: str) -> Tuple[AvailableSource, ...]:
    """The encounter documents the manifest lists for one claim, none yet retrieved."""

    from claim_agent.tools.encounter_notes import claim_document_inventory

    return tuple(
        AvailableSource(
            source_id=document.document_id,
            version=document.version,
            source_type="encounter_note",
            document_type=document.document_type,
        )
        for document in claim_document_inventory(knowledge_store, claim_id)
    )


def source_claim_index(manifest_sources: Sequence[Mapping[str, object]]) -> Dict[str, str]:
    """Map each claim-specific source ID in a knowledge manifest to its claim.

    Typical use: ``source_claim_index(store.entries_of_type("encounter_note"))``.
    """

    return {
        str(source["source_id"]): str(source["claim_id"])
        for source in manifest_sources
        if source.get("claim_id")
    }
