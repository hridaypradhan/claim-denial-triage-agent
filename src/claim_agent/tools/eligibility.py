"""Eligibility evidence retrieval (#56).

``get_eligibility_evidence`` returns what a payer's eligibility response says
about one member account: its coverage periods and citable passages. It
reports evidence and never decides eligibility; whether the service date
falls inside a coverage period is left to the agent and the validator.

The records live in ``eligibility/``, a versioned synthetic store with its own
manifest and hashes (``eligibility-v1``). They are kept apart from the frozen
``knowledge-v2`` corpus, and only development and demonstration claims have
records.

``EligibilityToolDispatcher`` wraps another dispatcher. It answers the
eligibility tool and forwards every other tool unchanged. The member account
must be the run's own; the payer may differ, because checking another payer
for other coverage is legitimate.

Behavior:
- One record for the account and payer: ``OK`` with its passages.
- No record: ``NO_MATCH``.
- A listed record that can't be read, or two records for one account and
  payer: ``DOCUMENT_UNAVAILABLE`` (never guessed).
- Missing, extra, or blank arguments: ``MALFORMED_REQUEST``.
- Another member account: ``EligibilityArgumentMismatchError``, which the
  runtime turns into manual review.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

from claim_agent.contracts import ErrorCode, EvidencePassage, ToolResult, ToolStatus
from claim_agent.tools.knowledge_store import KnowledgeStore, KnowledgeStoreError

ELIGIBILITY_TOOL = "get_eligibility_evidence"
ELIGIBILITY_SOURCE_TYPE = "eligibility_record"
ELIGIBILITY_ARGUMENT_NAMES = {"patient_account", "payer"}
ELIGIBILITY_ROOT = Path(__file__).resolve().parents[3] / "eligibility"


class EligibilityArgumentMismatchError(ValueError):
    """Raised when an eligibility request names another member account."""


@dataclass(frozen=True)
class EligibilityRecord:
    source_id: str
    version: str
    patient_account: str
    payer: str
    response_date: str
    coverage_periods: Tuple[Mapping[str, object], ...]
    passages: Tuple[EvidencePassage, ...]


def find_eligibility_record(store: KnowledgeStore, patient_account: str, payer: str) -> Optional[EligibilityRecord]:
    """Return the one record for the account and payer, or None if there is none.

    Raises ``KnowledgeStoreError`` when a listed record can't be read or more
    than one record matches, so a caller never has to guess.
    """

    entries = [
        entry for entry in store.entries_of_type(ELIGIBILITY_SOURCE_TYPE)
        if entry.get("patient_account") == patient_account and entry.get("payer") == payer
    ]
    if not entries:
        return None
    if len(entries) > 1:
        raise KnowledgeStoreError(f"More than one eligibility record for {patient_account} at {payer}.")
    entry = entries[0]
    document = store.load_source(entry)
    if (document.get("patient_account"), document.get("payer")) != (patient_account, payer):
        raise KnowledgeStoreError(f"Eligibility record {entry['source_id']} does not match its manifest entry.")
    source_id, version = str(document["source_id"]), str(document["version"])
    return EligibilityRecord(
        source_id=source_id,
        version=version,
        patient_account=patient_account,
        payer=payer,
        response_date=str(document["response_date"]),
        coverage_periods=tuple(dict(period) for period in document["coverage_periods"]),
        passages=tuple(
            EvidencePassage(
                passage_id=str(passage["passage_id"]),
                source_id=source_id,
                source_type=ELIGIBILITY_SOURCE_TYPE,
                version=version,
                text=str(passage["text"]),
            )
            for passage in document["passages"]
        ),
    )


@dataclass(frozen=True)
class EligibilityToolDispatcher:
    """Answer ``get_eligibility_evidence`` for the run's member; forward other tools."""

    dispatcher: object
    store: KnowledgeStore
    patient_account: str

    def dispatch(self, request_id: str, tool_name: str, tool_arguments: Mapping[str, object]) -> ToolResult:
        if tool_name != ELIGIBILITY_TOOL:
            return self.dispatcher.dispatch(request_id=request_id, tool_name=tool_name, tool_arguments=tool_arguments)

        if set(tool_arguments) != ELIGIBILITY_ARGUMENT_NAMES or not all(
            isinstance(value, str) and value.strip() for value in tool_arguments.values()
        ):
            return _error(request_id, ErrorCode.MALFORMED_REQUEST)
        if tool_arguments["patient_account"] != self.patient_account:
            raise EligibilityArgumentMismatchError(
                f"{ELIGIBILITY_TOOL} must name the run's member account {self.patient_account}."
            )

        try:
            record = find_eligibility_record(self.store, self.patient_account, str(tool_arguments["payer"]))
        except KnowledgeStoreError:
            return _error(request_id, ErrorCode.DOCUMENT_UNAVAILABLE)
        if record is None:
            return _error(request_id, ErrorCode.NO_MATCH)

        return ToolResult(
            request_id=request_id,
            tool_name=ELIGIBILITY_TOOL,
            status=ToolStatus.OK,
            data={
                "eligibility_snapshot_id": self.store.snapshot_id,
                "patient_account": record.patient_account,
                "payer": record.payer,
                "response_date": record.response_date,
                "coverage_periods": [dict(period) for period in record.coverage_periods],
            },
            source_ids=[f"{record.source_id}@v{record.version}"],
            evidence_passages=list(record.passages),
        )


def _error(request_id: str, error_code: ErrorCode) -> ToolResult:
    return ToolResult(request_id=request_id, tool_name=ELIGIBILITY_TOOL, status=ToolStatus.ERROR, error_code=error_code)
