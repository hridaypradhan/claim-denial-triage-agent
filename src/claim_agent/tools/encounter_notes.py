"""Find a claim's encounter documents and return them as citable passages.

Notes are fetched by claim ID and document ID, never searched. A document that
belongs to another claim is treated as unavailable. See decision 0001.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from claim_agent.tools.knowledge_store import KnowledgeStore


@dataclass(frozen=True)
class ClaimDocument:
    """One entry in a claim's document inventory."""

    document_id: str
    document_type: str
    version: str


@dataclass(frozen=True)
class NotePassage:
    """One numbered, citable passage of an encounter note."""

    passage_id: str
    text: str


@dataclass(frozen=True)
class EncounterNote:
    """One synthetic encounter document."""

    document_id: str
    version: str
    document_type: str
    claim_id: str
    note_date: str
    author_role: str
    synthetic: bool
    passages: Tuple[NotePassage, ...]


def claim_document_inventory(
    knowledge_store: KnowledgeStore,
    claim_id: str,
) -> Tuple[ClaimDocument, ...]:
    """Return the encounter documents the manifest lists for one claim."""

    return tuple(
        ClaimDocument(
            document_id=entry["source_id"],
            document_type=entry["document_type"],
            version=entry["version"],
        )
        for entry in _claim_entries(knowledge_store, claim_id)
    )


def find_encounter_note(
    knowledge_store: KnowledgeStore,
    claim_id: str,
    document_id: str,
) -> Optional[EncounterNote]:
    """Return one claim's document, or None when it is unavailable.

    None also covers more than one matching version, because the tool must not
    guess which version applies.
    """

    matched_entries = [
        entry
        for entry in _claim_entries(knowledge_store, claim_id)
        if entry["source_id"] == document_id
    ]

    if len(matched_entries) != 1:
        return None

    source = knowledge_store.load_source(matched_entries[0])

    return EncounterNote(
        document_id=source["source_id"],
        version=source["version"],
        document_type=source["document_type"],
        claim_id=source["claim_id"],
        note_date=source["note_date"],
        author_role=source["author_role"],
        synthetic=source["synthetic"],
        passages=tuple(
            NotePassage(
                passage_id=passage["passage_id"],
                text=passage["text"],
            )
            for passage in source["passages"]
        ),
    )


def _claim_entries(
    knowledge_store: KnowledgeStore,
    claim_id: str,
) -> Tuple[Mapping[str, object], ...]:
    """Return the encounter-note manifest entries for one claim."""

    return tuple(
        entry
        for entry in knowledge_store.entries_of_type("encounter_note")
        if entry["claim_id"] == claim_id
    )
