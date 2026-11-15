"""Same-run evidence bundle contract (#59).

An ``EvidenceBundle`` is the frozen record of everything one agent run
retrieved for one claim service line: the passages with their provenance,
every retrieval call, the policy formula, and the claim's listed documents.
Verification (#50, #60) and drafting (#51, #61) take the bundle itself, so the
run, snapshot, evidence, permitted citations, and fingerprint all come from
one source. ``claim_agent.tools.evidence_bundle`` builds bundles during a run.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from enum import Enum
from typing import List, Mapping, Optional, Sequence, Tuple

from pydantic import Field, model_validator

from claim_agent.contracts.schema import EvidencePassage, StrictContract, ToolStatus

BUNDLE_VERSION = "0.1"
PASSAGE_ID_PATTERN = re.compile(r"^(?P<source_id>[^@#\s]+)@v(?P<version>[^#\s]+)#(?P<anchor>[^#\s]+)$")


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


class AvailableSource(StrictContract):
    """A document the manifest lists for the claim, whether or not the run retrieved it."""

    source_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    document_type: Optional[str] = None
    retrieved: bool = False


class BundledPassage(StrictContract):
    """One retrieved passage plus where and for which claim it was retrieved."""

    passage: EvidencePassage
    source_type: str = Field(min_length=1)
    document_type: Optional[str] = None
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
    available_sources: Tuple[AvailableSource, ...] = ()

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
            if item.source_type != item.passage.source_type:
                raise ValueError(f"passage {pid} has a source_type that differs from its passage")
        retrieved_sources = {(p.passage.source_id, p.passage.version) for p in self.passages}
        for source in self.available_sources:
            if source.retrieved != ((source.source_id, source.version) in retrieved_sources):
                raise ValueError(f"available source {source.source_id} has an inconsistent retrieved flag")
        return self

    @property
    def passage_ids(self) -> Tuple[str, ...]:
        return tuple(item.passage.passage_id for item in self.passages)

    @property
    def unretrieved_sources(self) -> Tuple[AvailableSource, ...]:
        """Documents listed for the claim that the run never retrieved."""

        return tuple(source for source in self.available_sources if not source.retrieved)

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
