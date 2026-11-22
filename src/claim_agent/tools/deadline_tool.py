"""The read-only ``calculate_deadline`` agent tool.

The filing deadline is computed once, by the frozen baseline, and carried into
the run as ``BaselineFinding.filing_deadline`` and ``deadline_status``. The
safety validator enforces that same value. This tool reports it to the agent
without recomputing it: it adds no deadline arithmetic of its own, only the
day count between the run's as-of date and the deadline.

``DeadlineToolDispatcher`` wraps another dispatcher. It answers
``calculate_deadline`` from the run's trusted finding and forwards every other
tool unchanged, so it can sit outside the claim-bound dispatcher.

Behavior:
- Malformed arguments return ``MALFORMED_REQUEST``.
- Arguments naming another claim or service line raise
  ``DeadlineArgumentMismatchError``, which the runtime turns into manual review.
- A disposition with no deadline returns ``OK`` with ``deadline_status: NONE``
  and no date.
- An expired deadline returns ``OK`` with ``deadline_status: EXPIRED`` and the
  number of days overdue. The tool is read-only, so it can't change the
  validator's handling of an expired deadline.
- A finding whose status conflicts with its date and the as-of date is refused
  when the wrapper is built, so it can never be reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Optional

from claim_agent.contracts import BaselineFinding, Disposition, ErrorCode, ToolResult, ToolStatus

DEADLINE_TOOL = "calculate_deadline"
DEADLINE_SOURCE_ID = "baseline-v1-finding"
DEADLINE_ARGUMENT_NAMES = {"claim_id", "service_line_id"}

_BASIS = {
    Disposition.APPEAL_WITH_DOCUMENTATION: "remit date plus the payer's appeal window",
    Disposition.CORRECT_AND_RESUBMIT: "date of service plus the payer's corrected-claim window",
    Disposition.ROUTE_TO_CORRECT_PAYER: "date of service plus the payer's corrected-claim window",
}


class DeadlineArgumentMismatchError(ValueError):
    """Raised when a deadline request names a claim or line other than the run's."""


class DeadlineFindingError(ValueError):
    """Raised when the run's finding can't support a trustworthy deadline answer."""


def _expected_status(deadline: Optional[date], as_of: date) -> str:
    if deadline is None:
        return "NONE"
    return "EXPIRED" if deadline < as_of else "ACTIVE"


@dataclass(frozen=True)
class DeadlineToolDispatcher:
    """Answer ``calculate_deadline`` from the run's finding; forward other tools."""

    dispatcher: object
    finding: BaselineFinding
    as_of: date

    def __post_init__(self) -> None:
        expected = _expected_status(self.finding.filing_deadline, self.as_of)
        if self.finding.deadline_status != expected:
            raise DeadlineFindingError(
                f"Finding deadline_status {self.finding.deadline_status!r} conflicts with "
                f"filing_deadline {self.finding.filing_deadline} on {self.as_of} (expected {expected!r})."
            )

    def dispatch(self, request_id: str, tool_name: str, tool_arguments: Mapping[str, object]) -> ToolResult:
        if tool_name != DEADLINE_TOOL:
            return self.dispatcher.dispatch(request_id=request_id, tool_name=tool_name, tool_arguments=tool_arguments)

        if not _well_formed(tool_arguments):
            return ToolResult(
                request_id=request_id, tool_name=DEADLINE_TOOL,
                status=ToolStatus.ERROR, error_code=ErrorCode.MALFORMED_REQUEST,
            )
        if (tool_arguments["claim_id"], tool_arguments["service_line_id"]) != (
            self.finding.claim_id, self.finding.service_line_id,
        ):
            raise DeadlineArgumentMismatchError(
                f"{DEADLINE_TOOL} must name the run's claim {self.finding.claim_id} "
                f"line {self.finding.service_line_id}."
            )

        deadline = self.finding.filing_deadline
        days = None if deadline is None else (deadline - self.as_of).days
        return ToolResult(
            request_id=request_id,
            tool_name=DEADLINE_TOOL,
            status=ToolStatus.OK,
            data={
                "claim_id": self.finding.claim_id,
                "service_line_id": self.finding.service_line_id,
                "as_of": self.as_of.isoformat(),
                "disposition": self.finding.baseline_disposition.value,
                "filing_deadline": None if deadline is None else deadline.isoformat(),
                "deadline_status": self.finding.deadline_status,
                "days_remaining": None if days is None else max(days, 0),
                "days_overdue": None if days is None else max(-days, 0),
                "basis": _BASIS.get(self.finding.baseline_disposition),
            },
            source_ids=[DEADLINE_SOURCE_ID],
        )


def _well_formed(arguments: Mapping[str, object]) -> bool:
    if set(arguments) != DEADLINE_ARGUMENT_NAMES:
        return False
    claim_id, line = arguments["claim_id"], arguments["service_line_id"]
    return (
        isinstance(claim_id, str) and bool(claim_id.strip())
        and isinstance(line, int) and not isinstance(line, bool) and line >= 1
    )
