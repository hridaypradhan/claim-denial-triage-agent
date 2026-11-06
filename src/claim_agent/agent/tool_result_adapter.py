"""Adapt shared tool contracts to the runtime's internal observation format.

The runtime keeps a small internal observation type so its planning loop does
not depend directly on Pydantic models. This adapter is the single translation
point between Silas's production dispatcher and that internal runtime format.
"""

from __future__ import annotations

from typing import Mapping, Protocol

from claim_agent.agent.runtime_ports import OfflineToolObservation
from claim_agent.contracts import ToolResult, ToolStatus


class SharedToolDispatcher(Protocol):
    """Contract implemented by the production tool dispatcher."""

    def dispatch(
        self,
        request_id: str,
        tool_name: str,
        tool_arguments: Mapping[str, object],
    ) -> ToolResult:
        """Execute one identified tool request."""


class ToolResultDispatcherAdapter:
    """Make a shared-contract dispatcher usable by the agent runtime."""

    def __init__(self, dispatcher: SharedToolDispatcher) -> None:
        """Store the production dispatcher that performs the actual lookup."""

        self._dispatcher = dispatcher

    def dispatch(
        self,
        request_id: str,
        tool_name: str,
        tool_arguments: Mapping[str, object],
    ) -> OfflineToolObservation:
        """Dispatch a request and translate its shared result for the runtime."""

        tool_result = self._dispatcher.dispatch(
            request_id,
            tool_name,
            tool_arguments,
        )

        if tool_result.request_id != request_id:
            raise ValueError(
                "The returned ToolResult request_id does not match the request."
            )

        if tool_result.tool_name != tool_name:
            raise ValueError(
                "The returned ToolResult tool_name does not match the request."
            )

        return adapt_tool_result(tool_result)


def adapt_tool_result(tool_result: ToolResult) -> OfflineToolObservation:
    """Translate one validated ToolResult without discarding evidence.

    Passage order is preserved because it determines which retrieved evidence
    the model sees first. Passage text remains structured data and will later
    be placed inside the runtime's untrusted-data markers.
    """

    serialized_passages = [
        passage.model_dump(mode="json") for passage in tool_result.evidence_passages
    ]
    runtime_payload = {
        "request_id": tool_result.request_id,
        "tool_name": tool_result.tool_name,
        "data": dict(tool_result.data),
        "evidence_passages": serialized_passages,
    }

    return OfflineToolObservation(
        succeeded=tool_result.status is ToolStatus.OK,
        payload=runtime_payload,
        source_ids=tuple(tool_result.source_ids),
        error_code=(
            tool_result.error_code.value if tool_result.error_code is not None else None
        ),
        retryable=tool_result.retryable,
    )
