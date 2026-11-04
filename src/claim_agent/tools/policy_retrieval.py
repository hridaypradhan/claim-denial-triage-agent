"""Match one medical policy to a claim and rank its passages.

Matching never relaxes payer, procedure, or service date. See decision 0001.
"""

from __future__ import annotations

# TODO(#3): Wrap PolicyRetrievalResult in the approved ToolResult and
# EvidencePassage contracts once they are stable.

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Mapping, Optional, Sequence, Tuple

from claim_agent.tools.bm25 import score_documents
from claim_agent.tools.knowledge_store import KnowledgeStore


class PolicyRetrievalStatus(str, Enum):
    """Outcomes of one policy retrieval."""

    OK = "OK"
    NO_MATCH = "NO_MATCH"
    AMBIGUOUS_POLICY = "AMBIGUOUS_POLICY"


@dataclass(frozen=True)
class PolicyCriterion:
    """One criterion the agent must assess."""

    criterion_id: str
    summary: str
    passage_id: str


@dataclass(frozen=True)
class RankedPassage:
    """One citable policy passage and its relevance to the query."""

    passage_id: str
    section: str
    text: str
    score: float


@dataclass(frozen=True)
class RetrievedPolicy:
    """The single policy that applies to the claim."""

    source_id: str
    version: str
    title: str
    synthetic: bool
    effective_from: date
    effective_to: Optional[date]
    criteria_formula: str
    criteria: Tuple[PolicyCriterion, ...]
    passages: Tuple[RankedPassage, ...]


@dataclass(frozen=True)
class PolicyRetrievalResult:
    """Retrieved policy, or the reason no policy can be used.

    Attributes:
        status: Whether exactly one usable policy was found.
        policy: The matched policy when status is OK.
        matched_source_ids: Every policy that passed the filter.
    """

    status: PolicyRetrievalStatus
    policy: Optional[RetrievedPolicy] = None
    matched_source_ids: Tuple[str, ...] = ()


def retrieve_policy_passages(
    knowledge_store: KnowledgeStore,
    payer: str,
    procedure_code: str,
    service_date: date,
    query: str,
) -> PolicyRetrievalResult:
    """Return the one applicable policy with its passages ranked by query.

    Every passage is returned, most relevant first. The criteria and formula
    are always included, so a weak query cannot hide a required criterion.
    """

    matched_entries = [
        entry
        for entry in knowledge_store.entries_of_type("medical_policy")
        if _applies_to_claim(entry, payer, procedure_code, service_date)
    ]
    matched_source_ids = tuple(
        f'{entry["source_id"]}@v{entry["version"]}'
        for entry in matched_entries
    )

    if not matched_entries:
        return PolicyRetrievalResult(status=PolicyRetrievalStatus.NO_MATCH)

    if len(matched_entries) > 1:
        return PolicyRetrievalResult(
            status=PolicyRetrievalStatus.AMBIGUOUS_POLICY,
            matched_source_ids=matched_source_ids,
        )

    source = knowledge_store.load_source(matched_entries[0])
    criteria_formula = source["criteria_formulas"].get(procedure_code)

    # A listed code without a formula is a corpus defect, not a usable policy.
    if criteria_formula is None:
        return PolicyRetrievalResult(
            status=PolicyRetrievalStatus.NO_MATCH,
            matched_source_ids=matched_source_ids,
        )

    return PolicyRetrievalResult(
        status=PolicyRetrievalStatus.OK,
        policy=_build_policy(source, criteria_formula, query),
        matched_source_ids=matched_source_ids,
    )


def _applies_to_claim(
    entry: Mapping[str, object],
    payer: str,
    procedure_code: str,
    service_date: date,
) -> bool:
    """Apply the hard payer, procedure, and service-date filter."""

    effective_from = date.fromisoformat(entry["effective_from"])
    effective_to = _optional_date(entry["effective_to"])

    return (
        entry["payer"] == payer
        and procedure_code in entry["procedure_codes"]
        and effective_from <= service_date
        and (effective_to is None or service_date <= effective_to)
    )


def _build_policy(
    source: Mapping[str, object],
    criteria_formula: str,
    query: str,
) -> RetrievedPolicy:
    """Convert a policy document into a ranked retrieval result."""

    return RetrievedPolicy(
        source_id=source["source_id"],
        version=source["version"],
        title=source["title"],
        synthetic=source["synthetic"],
        effective_from=date.fromisoformat(source["effective_from"]),
        effective_to=_optional_date(source["effective_to"]),
        criteria_formula=criteria_formula,
        criteria=tuple(
            PolicyCriterion(
                criterion_id=criterion["criterion_id"],
                summary=criterion["summary"],
                passage_id=criterion["passage_id"],
            )
            for criterion in source["criteria"]
        ),
        passages=_rank_passages(source["passages"], query),
    )


def _rank_passages(
    passages: Sequence[Mapping[str, str]],
    query: str,
) -> Tuple[RankedPassage, ...]:
    """Sort passages by score, breaking ties by their order in the file."""

    scores = score_documents(query, [passage["text"] for passage in passages])
    ranked_positions = sorted(
        range(len(passages)),
        key=lambda position: (-scores[position], position),
    )

    return tuple(
        RankedPassage(
            passage_id=passages[position]["passage_id"],
            section=passages[position]["section"],
            text=passages[position]["text"],
            score=scores[position],
        )
        for position in ranked_positions
    )


def _optional_date(value: Optional[str]) -> Optional[date]:
    """Parse an ISO date that may be null."""

    return None if value is None else date.fromisoformat(value)
