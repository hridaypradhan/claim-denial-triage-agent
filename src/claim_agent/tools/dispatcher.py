"""Validate tool requests and answer them with shared ToolResult contracts.

The dispatcher is stateless and read-only. Every expected failure comes back as
an ERROR result with an explicit code; the runtime owns the one bounded retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Mapping, Optional, Sequence

from claim_agent.contracts import (
    ErrorCode,
    EvidencePassage,
    ToolResult,
    ToolStatus,
)
from claim_agent.tools.encounter_notes import (
    EncounterNote,
    find_encounter_note,
)
from claim_agent.tools.knowledge_store import (
    KnowledgeStore,
    KnowledgeStoreError,
)
from claim_agent.tools.policy_retrieval import (
    PolicyRetrievalResult,
    PolicyRetrievalStatus,
    RetrievedPolicy,
    retrieve_policy_passages,
)
from claim_agent.tools.reference_lookups import (
    DENIAL_CODE_SOURCE_ID,
    PAYER_PROFILE_SOURCE_ID,
    find_denial_code,
    find_payer_profile,
)

MEDICAL_POLICY_TOOL = "retrieve_medical_policy"
ENCOUNTER_NOTE_TOOL = "get_encounter_note"
DENIAL_CODE_TOOL = "lookup_denial_code"
PAYER_PROFILE_TOOL = "get_payer_profile"

_POLICY_ARGUMENT_NAMES = {"payer", "procedure_code", "service_date", "query"}
_ENCOUNTER_ARGUMENT_NAMES = {"claim_id", "document_id"}
_DENIAL_ARGUMENT_NAMES = {"carc", "rarc", "group_code"}
_PAYER_ARGUMENT_NAMES = {"payer_id"}
_POLICY_ERROR_CODES = {
    PolicyRetrievalStatus.NO_MATCH: ErrorCode.NO_MATCH,
    PolicyRetrievalStatus.AMBIGUOUS_POLICY: ErrorCode.AMBIGUOUS_POLICY,
}


@dataclass(frozen=True)
class _PolicyRequest:
    """Validated arguments for one medical-policy retrieval."""

    payer: str
    procedure_code: str
    service_date: date
    query: str


@dataclass(frozen=True)
class _EncounterRequest:
    """Validated arguments for one encounter-note lookup."""

    claim_id: str
    document_id: str


@dataclass(frozen=True)
class _DenialCodeRequest:
    """Validated arguments for one denial-code lookup."""

    carc: str
    rarc: Sequence[str]
    group_code: str


@dataclass(frozen=True)
class _PayerProfileRequest:
    """Validated arguments for one payer-profile lookup."""

    payer_id: str


class ToolDispatcher:
    """Route allowlisted tool requests to their read-only implementations."""

    def __init__(self, knowledge_store: KnowledgeStore) -> None:
        """Store the knowledge snapshot every tool reads from."""

        self._knowledge_store = knowledge_store
        self._tools = {
            MEDICAL_POLICY_TOOL: (
                _parse_policy_request,
                self._retrieve_medical_policy,
            ),
            ENCOUNTER_NOTE_TOOL: (
                _parse_encounter_request,
                self._get_encounter_note,
            ),
            DENIAL_CODE_TOOL: (
                _parse_denial_code_request,
                self._lookup_denial_code,
            ),
            PAYER_PROFILE_TOOL: (
                _parse_payer_profile_request,
                self._get_payer_profile,
            ),
        }

    def dispatch(
        self,
        request_id: str,
        tool_name: str,
        tool_arguments: Mapping[str, object],
    ) -> ToolResult:
        """Run one tool request and return its result.

        Args:
            request_id: Identifier the runtime assigned to this request.
            tool_name: Name of the requested tool.
            tool_arguments: Arguments proposed by the model.

        Returns:
            An OK result, or an ERROR result with an explicit error code.
        """

        if tool_name not in self._tools:
            return _error_result(
                request_id,
                tool_name,
                ErrorCode.MALFORMED_REQUEST,
            )

        parse_arguments, run_tool = self._tools[tool_name]

        try:
            parsed_request = parse_arguments(tool_arguments)
        except ValueError:
            return _error_result(
                request_id,
                tool_name,
                ErrorCode.MALFORMED_REQUEST,
            )

        try:
            return run_tool(request_id, parsed_request)
        except KnowledgeStoreError:
            return _error_result(
                request_id,
                tool_name,
                ErrorCode.DOCUMENT_UNAVAILABLE,
            )

    def _retrieve_medical_policy(
        self,
        request_id: str,
        policy_request: _PolicyRequest,
    ) -> ToolResult:
        """Find one policy and convert the outcome to the shared contract."""

        retrieval = retrieve_policy_passages(
            self._knowledge_store,
            payer=policy_request.payer,
            procedure_code=policy_request.procedure_code,
            service_date=policy_request.service_date,
            query=policy_request.query,
        )

        return self._policy_result(request_id, retrieval)

    def _policy_result(
        self,
        request_id: str,
        retrieval: PolicyRetrievalResult,
    ) -> ToolResult:
        """Convert one retrieval outcome into the shared contract."""

        if retrieval.policy is None:
            return _error_result(
                request_id,
                MEDICAL_POLICY_TOOL,
                _POLICY_ERROR_CODES[retrieval.status],
            )

        policy = retrieval.policy

        return ToolResult(
            request_id=request_id,
            tool_name=MEDICAL_POLICY_TOOL,
            status=ToolStatus.OK,
            data=_policy_data(policy, self._knowledge_store.snapshot_id),
            source_ids=[f"{policy.source_id}@v{policy.version}"],
            evidence_passages=_policy_passages(policy),
        )

    def _get_encounter_note(
        self,
        request_id: str,
        encounter_request: _EncounterRequest,
    ) -> ToolResult:
        """Return one encounter note as numbered, citable passages."""

        note = find_encounter_note(
            self._knowledge_store,
            claim_id=encounter_request.claim_id,
            document_id=encounter_request.document_id,
        )

        if note is None:
            return _error_result(
                request_id,
                ENCOUNTER_NOTE_TOOL,
                ErrorCode.DOCUMENT_UNAVAILABLE,
            )

        return ToolResult(
            request_id=request_id,
            tool_name=ENCOUNTER_NOTE_TOOL,
            status=ToolStatus.OK,
            data=_encounter_data(note, self._knowledge_store.snapshot_id),
            source_ids=[f"{note.document_id}@v{note.version}"],
            evidence_passages=_encounter_passages(note),
        )

    def _lookup_denial_code(
        self,
        request_id: str,
        denial_request: _DenialCodeRequest,
    ) -> ToolResult:
        """Return what the code tables say about one denial."""

        return _reference_result(
            request_id,
            DENIAL_CODE_TOOL,
            DENIAL_CODE_SOURCE_ID,
            find_denial_code(
                denial_request.carc,
                denial_request.rarc,
                denial_request.group_code,
            ),
        )

    def _get_payer_profile(
        self,
        request_id: str,
        payer_request: _PayerProfileRequest,
    ) -> ToolResult:
        """Return one payer's filing rules and verification status."""

        return _reference_result(
            request_id,
            PAYER_PROFILE_TOOL,
            PAYER_PROFILE_SOURCE_ID,
            find_payer_profile(payer_request.payer_id),
        )


def _parse_policy_request(
    tool_arguments: Mapping[str, object],
) -> _PolicyRequest:
    """Return validated policy arguments or raise ValueError."""

    _require_text_arguments(tool_arguments, _POLICY_ARGUMENT_NAMES)

    service_date_text = tool_arguments["service_date"]
    service_date = date.fromisoformat(service_date_text)

    # fromisoformat also accepts forms such as 20260602 and 2026-W23-2.
    if service_date.isoformat() != service_date_text:
        raise ValueError("service_date must use YYYY-MM-DD.")

    return _PolicyRequest(
        payer=tool_arguments["payer"],
        procedure_code=tool_arguments["procedure_code"],
        service_date=service_date,
        query=tool_arguments["query"],
    )


def _parse_encounter_request(
    tool_arguments: Mapping[str, object],
) -> _EncounterRequest:
    """Return validated encounter arguments or raise ValueError."""

    _require_text_arguments(tool_arguments, _ENCOUNTER_ARGUMENT_NAMES)

    return _EncounterRequest(
        claim_id=tool_arguments["claim_id"],
        document_id=tool_arguments["document_id"],
    )


def _parse_denial_code_request(
    tool_arguments: Mapping[str, object],
) -> _DenialCodeRequest:
    """Return validated denial-code arguments or raise ValueError."""

    _require_argument_names(tool_arguments, _DENIAL_ARGUMENT_NAMES)

    rarc_codes = tool_arguments["rarc"]

    if not isinstance(rarc_codes, list):
        raise ValueError("rarc must be a list of codes.")

    for text_value in (
        tool_arguments["carc"],
        tool_arguments["group_code"],
        *rarc_codes,
    ):
        _require_text(text_value)

    return _DenialCodeRequest(
        carc=tool_arguments["carc"],
        rarc=tuple(rarc_codes),
        group_code=tool_arguments["group_code"],
    )


def _parse_payer_profile_request(
    tool_arguments: Mapping[str, object],
) -> _PayerProfileRequest:
    """Return validated payer arguments or raise ValueError."""

    _require_text_arguments(tool_arguments, _PAYER_ARGUMENT_NAMES)

    return _PayerProfileRequest(payer_id=tool_arguments["payer_id"])


def _require_text_arguments(
    tool_arguments: Mapping[str, object],
    argument_names: set,
) -> None:
    """Require exactly the named arguments, all as non-blank strings."""

    _require_argument_names(tool_arguments, argument_names)

    for argument_value in tool_arguments.values():
        _require_text(argument_value)


def _require_argument_names(
    tool_arguments: Mapping[str, object],
    argument_names: set,
) -> None:
    """Require exactly the named arguments."""

    if set(tool_arguments) != argument_names:
        raise ValueError("The tool received unexpected arguments.")


def _require_text(argument_value: object) -> None:
    """Require one non-blank string."""

    if not isinstance(argument_value, str) or not argument_value.strip():
        raise ValueError("Tool arguments must be non-blank strings.")


def _policy_data(
    policy: RetrievedPolicy,
    snapshot_id: str,
) -> Dict[str, object]:
    """Return the policy details that do not belong on a passage."""

    return {
        "knowledge_snapshot_id": snapshot_id,
        "title": policy.title,
        "synthetic": policy.synthetic,
        "criteria_formula": policy.criteria_formula,
        "criteria": [
            {
                "criterion_id": criterion.criterion_id,
                "summary": criterion.summary,
                "passage_id": criterion.passage_id,
            }
            for criterion in policy.criteria
        ],
        "passage_rankings": [
            {
                "passage_id": passage.passage_id,
                "section": passage.section,
                "score": passage.score,
            }
            for passage in policy.passages
        ],
    }


def _encounter_data(
    note: EncounterNote,
    snapshot_id: str,
) -> Dict[str, object]:
    """Return the note details that do not belong on a passage."""

    return {
        "knowledge_snapshot_id": snapshot_id,
        "claim_id": note.claim_id,
        "document_type": note.document_type,
        "note_date": note.note_date,
        "author_role": note.author_role,
        "synthetic": note.synthetic,
    }


def _policy_passages(policy: RetrievedPolicy) -> List[EvidencePassage]:
    """Return the policy's passages in ranked order."""

    return [
        EvidencePassage(
            passage_id=passage.passage_id,
            source_id=policy.source_id,
            source_type="medical_policy",
            version=policy.version,
            effective_from=policy.effective_from,
            effective_to=policy.effective_to,
            text=passage.text,
        )
        for passage in policy.passages
    ]


def _encounter_passages(note: EncounterNote) -> List[EvidencePassage]:
    """Return the note's passages in document order."""

    return [
        EvidencePassage(
            passage_id=passage.passage_id,
            source_id=note.document_id,
            source_type="encounter_note",
            version=note.version,
            text=passage.text,
        )
        for passage in note.passages
    ]


def _reference_result(
    request_id: str,
    tool_name: str,
    source_id: str,
    data: Optional[Dict[str, object]],
) -> ToolResult:
    """Convert one table lookup into the shared contract."""

    if data is None:
        return _error_result(request_id, tool_name, ErrorCode.NO_MATCH)

    return ToolResult(
        request_id=request_id,
        tool_name=tool_name,
        status=ToolStatus.OK,
        data=data,
        source_ids=[source_id],
    )


def _error_result(
    request_id: str,
    tool_name: str,
    error_code: ErrorCode,
) -> ToolResult:
    """Build a non-retryable ERROR result."""

    return ToolResult(
        request_id=request_id,
        tool_name=tool_name,
        status=ToolStatus.ERROR,
        error_code=error_code,
    )
