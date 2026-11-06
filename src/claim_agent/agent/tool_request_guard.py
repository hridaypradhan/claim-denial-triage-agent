"""Prevent tools from retrieving data for a different claim.

The language model may suggest tool arguments, but authoritative claim fields
come from the application starting the run. This guard compares the suggested
arguments with that trusted context before allowing a retrieval to proceed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

from claim_agent.agent.tool_result_adapter import SharedToolDispatcher
from claim_agent.contracts import ToolResult
from claim_agent.tools.dispatcher import (
    ENCOUNTER_NOTE_TOOL,
    MEDICAL_POLICY_TOOL,
)


class ToolArgumentMismatchError(ValueError):
    """Raised when model-selected arguments do not belong to the active claim."""


@dataclass(frozen=True)
class ClaimToolContext:
    """Authoritative claim fields that constrain evidence retrieval."""

    claim_id: str
    payer: str
    procedure_code: str
    service_date: date


class ClaimBoundToolDispatcher:
    """Allow retrieval only when its arguments match the active claim."""

    def __init__(
        self,
        dispatcher: SharedToolDispatcher,
        claim_context: ClaimToolContext,
    ) -> None:
        """Store the production dispatcher and trusted claim context."""

        self._dispatcher = dispatcher
        self._claim_context = claim_context

    def dispatch(
        self,
        request_id: str,
        tool_name: str,
        tool_arguments: Mapping[str, object],
    ) -> ToolResult:
        """Validate claim-bound arguments before forwarding the request."""

        expected_arguments = self._expected_arguments(tool_name)

        for argument_name, expected_value in expected_arguments.items():
            actual_value = tool_arguments.get(argument_name)

            if actual_value != expected_value:
                raise ToolArgumentMismatchError(
                    f"{tool_name} argument {argument_name!r} does not match "
                    "the active claim."
                )

        return self._dispatcher.dispatch(
            request_id=request_id,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
        )

    def _expected_arguments(
        self,
        tool_name: str,
    ) -> Mapping[str, str]:
        """Return the trusted claim arguments required by one known tool."""

        if tool_name == MEDICAL_POLICY_TOOL:
            return {
                "payer": self._claim_context.payer,
                "procedure_code": self._claim_context.procedure_code,
                "service_date": self._claim_context.service_date.isoformat(),
            }

        if tool_name == ENCOUNTER_NOTE_TOOL:
            return {
                "claim_id": self._claim_context.claim_id,
            }

        return {}
