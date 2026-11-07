"""Prevent tools from retrieving data for a different claim.

The language model may suggest tool arguments, but authoritative claim fields
come from the application starting the run. This guard compares the suggested
arguments with that trusted context before allowing a retrieval to proceed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

from baseline.triage.claim import Claim

from claim_agent.agent.tool_result_adapter import SharedToolDispatcher
from claim_agent.contracts import ToolResult
from claim_agent.tools.dispatcher import (
    ENCOUNTER_NOTE_TOOL,
    MEDICAL_POLICY_TOOL,
)


class ToolArgumentMismatchError(ValueError):
    """Raised when model-selected arguments do not belong to the active claim."""


class UnsupportedClaimBoundToolError(ValueError):
    """Raised when the claim guard has no validation rule for a tool."""


class ClaimToolContextError(ValueError):
    """Raised when a claim lacks fields required to constrain retrieval."""


@dataclass(frozen=True)
class ClaimToolContext:
    """Authoritative claim fields that constrain evidence retrieval."""

    claim_id: str
    payer: str
    procedure_code: str
    service_date: date


def build_claim_tool_context(
    claim: Claim,
    service_line_id: int,
) -> ClaimToolContext:
    """Build trusted retrieval constraints from a claim service line."""

    service_line = claim.line(service_line_id)
    procedure_code = service_line.get("cpt")

    if not service_line:
        raise ClaimToolContextError(
            f"Claim {claim.claim_id!r} has no service line {service_line_id}."
        )

    if not isinstance(procedure_code, str) or not procedure_code.strip():
        raise ClaimToolContextError(
            f"Claim {claim.claim_id!r} service line {service_line_id} "
            "has no procedure code."
        )

    if not claim.claim_id or not claim.payer or claim.date_of_service is None:
        raise ClaimToolContextError(
            "The claim lacks an ID, payer, or service date required "
            "for claim-bound retrieval."
        )

    return ClaimToolContext(
        claim_id=claim.claim_id,
        payer=claim.payer,
        procedure_code=procedure_code,
        service_date=claim.date_of_service,
    )


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

        # Fail closed until the tool has an explicit claim-binding rule.
        # This prevents a newly added retrieval tool from bypassing the guard.
        raise UnsupportedClaimBoundToolError(
            f"{tool_name!r} is not supported by the claim-bound dispatcher."
        )
