"""Validate tool requests and answer them with shared ToolResult contracts.

The dispatcher is stateless and read-only. Every expected failure comes back as
an ERROR result with an explicit code; the runtime owns the one bounded retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Mapping

from claim_agent.contracts import (
    ErrorCode,
    EvidencePassage,
    ToolResult,
    ToolStatus,
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

MEDICAL_POLICY_TOOL = "retrieve_medical_policy"

_POLICY_ARGUMENT_NAMES = {"payer", "procedure_code", "service_date", "query"}
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


class ToolDispatcher:
    """Route allowlisted tool requests to their read-only implementations."""

    def __init__(self, knowledge_store: KnowledgeStore) -> None:
        """Store the knowledge snapshot every tool reads from."""

        self._knowledge_store = knowledge_store

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

        if tool_name != MEDICAL_POLICY_TOOL:
            return _error_result(
                request_id,
                tool_name,
                ErrorCode.MALFORMED_REQUEST,
            )

        try:
            policy_request = _parse_policy_request(tool_arguments)
        except ValueError:
            return _error_result(
                request_id,
                tool_name,
                ErrorCode.MALFORMED_REQUEST,
            )

        try:
            retrieval = retrieve_policy_passages(
                self._knowledge_store,
                payer=policy_request.payer,
                procedure_code=policy_request.procedure_code,
                service_date=policy_request.service_date,
                query=policy_request.query,
            )
        except KnowledgeStoreError:
            return _error_result(
                request_id,
                tool_name,
                ErrorCode.DOCUMENT_UNAVAILABLE,
            )

        return self._policy_result(request_id, tool_name, retrieval)

    def _policy_result(
        self,
        request_id: str,
        tool_name: str,
        retrieval: PolicyRetrievalResult,
    ) -> ToolResult:
        """Convert one retrieval outcome into the shared contract."""

        if retrieval.policy is None:
            return _error_result(
                request_id,
                tool_name,
                _POLICY_ERROR_CODES[retrieval.status],
            )

        policy = retrieval.policy

        return ToolResult(
            request_id=request_id,
            tool_name=tool_name,
            status=ToolStatus.OK,
            data=_policy_data(policy, self._knowledge_store.snapshot_id),
            source_ids=[f"{policy.source_id}@v{policy.version}"],
            evidence_passages=_evidence_passages(policy),
        )


def _parse_policy_request(
    tool_arguments: Mapping[str, object],
) -> _PolicyRequest:
    """Return validated policy arguments or raise ValueError."""

    if set(tool_arguments) != _POLICY_ARGUMENT_NAMES:
        raise ValueError("Policy retrieval received unexpected arguments.")

    for argument_value in tool_arguments.values():
        if not isinstance(argument_value, str) or not argument_value.strip():
            raise ValueError("Policy arguments must be non-blank strings.")

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


def _evidence_passages(policy: RetrievedPolicy) -> List[EvidencePassage]:
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
