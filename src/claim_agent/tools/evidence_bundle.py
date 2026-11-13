"""Same-run evidence bundles and deterministic citation resolution (#59).

During an agent run, ``EvidenceBundleRecorder`` wraps the claim-bound tool
dispatcher and records every retrieval call and every returned passage, with
provenance taken from the trusted claim context rather than from model
arguments. When the run ends, ``freeze()`` produces one immutable
``EvidenceBundle``. Verification (#60), drafting (#61), citation validation,
and tracing all consume that same bundle, so none of them re-runs retrieval
or sees evidence from another run or claim.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from pydantic import Field, model_validator

from claim_agent.contracts import EvidencePassage, ToolResult, ToolStatus
from claim_agent.contracts.schema import StrictContract

BUNDLE_VERSION = "0.1"
PASSAGE_ID_PATTERN = re.compile(r"^(?P<source_id>[^@#\s]+)@v(?P<version>[^#\s]+)#(?P<anchor>[^#\s]+)$")


class EvidenceBundleError(ValueError):
    """Raised when recorded evidence would make the bundle inconsistent."""


class CitationProblemCode(str, Enum):
    DUPLICATE_CITATION = "DUPLICATE_CITATION"
    MALFORMED_CITATION = "MALFORMED_CITATION"
    CROSS_CLAIM_CITATION = "CROSS_CLAIM_CITATION"
    UNRESOLVED_CITATION = "UNRESOLVED_CITATION"


class CitationProblem(StrictContract):
    code: CitationProblemCode
    citation_index: int
    citation_id: str
    message: str


class CitationResolutionError(ValueError):
    """Raised by ``EvidenceBundle.resolve`` when any citation is rejected."""

    def __init__(self, problems: Sequence[CitationProblem]) -> None:
        self.problems: Tuple[CitationProblem, ...] = tuple(problems)
        super().__init__("; ".join(p.message for p in self.problems))


class ClaimEvidenceScope(StrictContract):
    """The trusted claim fields every bundled passage is bound to."""

    claim_id: str = Field(min_length=1)
    service_line_id: int
    payer: str = Field(min_length=1)
    procedure_code: str = Field(min_length=1)
    service_date: date


class RetrievalRecord(StrictContract):
    """One tool call made during the run, successful or not."""

    call_index: int = Field(ge=1)
    request_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ToolStatus
    error_code: Optional[str] = None
    source_ids: Tuple[str, ...] = ()
    passage_ids: Tuple[str, ...] = ()


class BundledPassage(StrictContract):
    """One retrieved passage plus where and for which claim it was retrieved."""

    passage: EvidencePassage
    claim_id: str
    payer: str
    procedure_code: str
    service_date: date
    tool_name: str
    request_id: str
    call_index: int = Field(ge=1)


class EvidenceBundle(StrictContract):
    """Frozen evidence retrieved during one run, for one claim service line."""

    model_config = StrictContract.model_config | {"frozen": True}

    bundle_version: str = BUNDLE_VERSION
    run_id: str = Field(min_length=1)
    knowledge_snapshot_id: str = Field(min_length=1)
    scope: ClaimEvidenceScope
    policy_formula: Optional[str] = None
    passages: Tuple[BundledPassage, ...] = ()
    retrievals: Tuple[RetrievalRecord, ...] = ()

    @model_validator(mode="after")
    def _check_consistency(self) -> "EvidenceBundle":
        seen = set()
        call_indexes = {r.call_index for r in self.retrievals}
        for item in self.passages:
            pid = item.passage.passage_id
            if pid in seen:
                raise ValueError(f"passage {pid} appears twice in the bundle")
            seen.add(pid)
            if (item.claim_id, item.payer, item.procedure_code, item.service_date) != (
                self.scope.claim_id,
                self.scope.payer,
                self.scope.procedure_code,
                self.scope.service_date,
            ):
                raise ValueError(f"passage {pid} is bound to a different claim scope")
            if item.call_index not in call_indexes:
                raise ValueError(f"passage {pid} names retrieval call {item.call_index}, which is not recorded")
        return self

    @property
    def passage_ids(self) -> Tuple[str, ...]:
        return tuple(item.passage.passage_id for item in self.passages)

    @property
    def evidence_passages(self) -> Tuple[EvidencePassage, ...]:
        """The plain passages, in retrieval order, for existing validators."""

        return tuple(item.passage for item in self.passages)

    def fingerprint(self) -> str:
        """SHA-256 of the canonical bundle, for tracing which evidence was used."""

        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def check_citations(
        self,
        citation_ids: Sequence[str],
        source_claims: Optional[Mapping[str, str]] = None,
    ) -> List[CitationProblem]:
        """Return every problem with ``citation_ids``; an empty list means all resolve.

        ``source_claims`` maps a source ID to the claim it belongs to (see
        ``source_claim_index``). With it, a citation to another claim's note is
        reported as ``CROSS_CLAIM_CITATION`` instead of ``UNRESOLVED_CITATION``.
        """

        known = set(self.passage_ids)
        problems: List[CitationProblem] = []
        seen = set()
        for index, citation in enumerate(citation_ids):
            def problem(code: CitationProblemCode, message: str) -> None:
                problems.append(CitationProblem(code=code, citation_index=index, citation_id=citation, message=message))

            if citation in seen:
                problem(CitationProblemCode.DUPLICATE_CITATION, f"Citation {citation!r} is repeated.")
                continue
            seen.add(citation)
            match = PASSAGE_ID_PATTERN.match(citation) if isinstance(citation, str) else None
            if match is None:
                problem(CitationProblemCode.MALFORMED_CITATION, f"Citation {citation!r} is not a passage ID.")
                continue
            if citation in known:
                continue
            owner = (source_claims or {}).get(match.group("source_id"))
            if owner is not None and owner != self.scope.claim_id:
                problem(
                    CitationProblemCode.CROSS_CLAIM_CITATION,
                    f"Citation {citation!r} belongs to claim {owner}, not {self.scope.claim_id}.",
                )
            else:
                problem(CitationProblemCode.UNRESOLVED_CITATION, f"Citation {citation!r} was not retrieved in this run.")
        return problems

    def resolve(
        self,
        citation_ids: Sequence[str],
        source_claims: Optional[Mapping[str, str]] = None,
    ) -> Tuple[BundledPassage, ...]:
        """Return the cited passages in citation order, or raise on any problem."""

        problems = self.check_citations(citation_ids, source_claims)
        if problems:
            raise CitationResolutionError(problems)
        by_id = {item.passage.passage_id: item for item in self.passages}
        return tuple(by_id[c] for c in citation_ids)


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
    ) -> None:
        self._dispatcher = dispatcher
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
        if result.request_id != request_id or result.tool_name != tool_name:
            raise EvidenceBundleError("Tool result identity does not match its request.")
        call_index = len(self._retrievals) + 1
        ok = result.status is ToolStatus.OK
        self._retrievals.append(
            RetrievalRecord(
                call_index=call_index,
                request_id=request_id,
                tool_name=tool_name,
                status=result.status,
                error_code=None if result.error_code is None else result.error_code.value,
                source_ids=tuple(result.source_ids),
                passage_ids=tuple(p.passage_id for p in result.evidence_passages) if ok else (),
            )
        )
        if not ok:
            return

        reported_claim = result.data.get("claim_id")
        if reported_claim is not None and reported_claim != self._scope.claim_id:
            raise EvidenceBundleError(f"{tool_name} returned evidence for claim {reported_claim!r}.")
        reported_snapshot = result.data.get("knowledge_snapshot_id")
        if reported_snapshot is not None and reported_snapshot != self._snapshot_id:
            raise EvidenceBundleError(f"{tool_name} returned evidence from snapshot {reported_snapshot!r}.")

        formula = result.data.get("criteria_formula")
        if formula is not None:
            if self._policy_formula is not None and self._policy_formula != formula:
                raise EvidenceBundleError("A run cannot contain conflicting policy formulas.")
            self._policy_formula = formula

        for passage in result.evidence_passages:
            existing = self._passages.get(passage.passage_id)
            if existing is not None:
                if existing.passage != passage:
                    raise EvidenceBundleError(f"Passage {passage.passage_id} was returned with conflicting content.")
                continue  # keep the first retrieval's provenance
            self._passages[passage.passage_id] = BundledPassage(
                passage=passage,
                claim_id=self._scope.claim_id,
                payer=self._scope.payer,
                procedure_code=self._scope.procedure_code,
                service_date=self._scope.service_date,
                tool_name=tool_name,
                request_id=request_id,
                call_index=call_index,
            )

    def freeze(self) -> EvidenceBundle:
        """Return the run's immutable bundle; later calls return the same bundle."""

        if self._frozen is None:
            self._frozen = EvidenceBundle(
                run_id=self._run_id,
                knowledge_snapshot_id=self._snapshot_id,
                scope=self._scope,
                policy_formula=self._policy_formula,
                passages=tuple(self._passages.values()),
                retrievals=tuple(self._retrievals),
            )
        return self._frozen


def source_claim_index(manifest_sources: Sequence[Mapping[str, object]]) -> Dict[str, str]:
    """Map each claim-specific source ID in a knowledge manifest to its claim.

    Typical use: ``source_claim_index(store.entries_of_type("encounter_note"))``.
    """

    return {
        str(source["source_id"]): str(source["claim_id"])
        for source in manifest_sources
        if source.get("claim_id")
    }
