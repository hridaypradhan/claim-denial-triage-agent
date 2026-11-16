"""Tests for the calculate_deadline tool (#54)."""

import unittest
from datetime import date

from claim_agent.contracts import (
    BaselineFinding,
    Disposition,
    ErrorCode,
    RootCause,
    ToolResult,
    ToolStatus,
)
from claim_agent.tools.deadline_tool import (
    DEADLINE_TOOL,
    DeadlineArgumentMismatchError,
    DeadlineFindingError,
    DeadlineToolDispatcher,
)

AS_OF = date(2026, 11, 6)
ARGS = {"claim_id": "CLM-1", "service_line_id": 1}


def finding(disposition=Disposition.APPEAL_WITH_DOCUMENTATION, deadline=date(2027, 1, 7), status="ACTIVE"):
    return BaselineFinding(
        claim_id="CLM-1", service_line_id=1, denial_codes=["CO-50"],
        root_cause=RootCause.MEDICAL_NECESSITY, baseline_disposition=disposition,
        rationale="test", filing_deadline=deadline, deadline_status=status,
    )


class RecordingDispatcher:
    def __init__(self):
        self.calls = []

    def dispatch(self, request_id, tool_name, tool_arguments):
        self.calls.append(tool_name)
        return ToolResult(request_id=request_id, tool_name=tool_name, status=ToolStatus.ERROR,
                          error_code=ErrorCode.NO_MATCH)


def tool(f=None, inner=None):
    return DeadlineToolDispatcher(inner or RecordingDispatcher(), f or finding(), AS_OF)


class DeadlineToolTest(unittest.TestCase):
    def test_active_deadline_reports_days_remaining(self):
        result = tool().dispatch("R1", DEADLINE_TOOL, ARGS)

        self.assertIs(result.status, ToolStatus.OK)
        self.assertEqual(result.data["filing_deadline"], "2027-01-07")
        self.assertEqual(result.data["deadline_status"], "ACTIVE")
        self.assertEqual((result.data["days_remaining"], result.data["days_overdue"]), (62, 0))
        self.assertEqual(result.data["basis"], "remit date plus the payer's appeal window")

    def test_expired_deadline_reports_days_overdue(self):
        result = tool(finding(deadline=date(2026, 7, 8), status="EXPIRED")).dispatch("R1", DEADLINE_TOOL, ARGS)

        self.assertEqual(result.data["deadline_status"], "EXPIRED")
        self.assertEqual((result.data["days_remaining"], result.data["days_overdue"]), (0, 121))

    def test_deadline_due_today_is_active(self):
        result = tool(finding(deadline=AS_OF, status="ACTIVE")).dispatch("R1", DEADLINE_TOOL, ARGS)
        self.assertEqual((result.data["days_remaining"], result.data["days_overdue"]), (0, 0))

    def test_disposition_without_a_deadline_is_explicit(self):
        result = tool(finding(Disposition.WRITE_OFF, deadline=None, status="NONE")).dispatch("R1", DEADLINE_TOOL, ARGS)

        self.assertIs(result.status, ToolStatus.OK)
        self.assertIsNone(result.data["filing_deadline"])
        self.assertEqual(result.data["deadline_status"], "NONE")
        self.assertIsNone(result.data["days_remaining"])
        self.assertIsNone(result.data["basis"])

    def test_the_reported_date_is_the_findings_date(self):
        """The tool reports the baseline's deadline; it never recomputes it."""

        for deadline in (date(2026, 12, 13), date(2027, 6, 18), date(2027, 2, 1)):
            with self.subTest(deadline=deadline):
                result = tool(finding(deadline=deadline)).dispatch("R1", DEADLINE_TOOL, ARGS)
                self.assertEqual(result.data["filing_deadline"], deadline.isoformat())

    def test_malformed_arguments_are_rejected(self):
        bad = {
            "missing line": {"claim_id": "CLM-1"},
            "extra argument": {**ARGS, "as_of": "2030-01-01"},
            "line as text": {"claim_id": "CLM-1", "service_line_id": "1"},
            "line as bool": {"claim_id": "CLM-1", "service_line_id": True},
            "line zero": {"claim_id": "CLM-1", "service_line_id": 0},
            "blank claim": {"claim_id": " ", "service_line_id": 1},
        }
        for name, arguments in bad.items():
            with self.subTest(case=name):
                result = tool().dispatch("R1", DEADLINE_TOOL, arguments)
                self.assertIs(result.error_code, ErrorCode.MALFORMED_REQUEST)

    def test_another_claim_or_line_is_refused(self):
        for arguments in ({"claim_id": "CLM-2", "service_line_id": 1}, {"claim_id": "CLM-1", "service_line_id": 2}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(DeadlineArgumentMismatchError):
                    tool().dispatch("R1", DEADLINE_TOOL, arguments)

    def test_a_conflicting_finding_is_refused_up_front(self):
        conflicts = {
            "active but past": finding(deadline=date(2026, 7, 8), status="ACTIVE"),
            "expired but future": finding(status="EXPIRED"),
            "no deadline but active": finding(deadline=None, status="ACTIVE"),
            "deadline but none": finding(status="NONE"),
        }
        for name, f in conflicts.items():
            with self.subTest(case=name):
                with self.assertRaises(DeadlineFindingError):
                    tool(f)

    def test_other_tools_pass_through_untouched(self):
        inner = RecordingDispatcher()
        result = tool(inner=inner).dispatch("R1", "get_payer_profile", {"payer_id": "UHC"})

        self.assertEqual(inner.calls, ["get_payer_profile"])
        self.assertIs(result.error_code, ErrorCode.NO_MATCH)

    def test_the_deadline_tool_never_reaches_the_inner_dispatcher(self):
        inner = RecordingDispatcher()
        tool(inner=inner).dispatch("R1", DEADLINE_TOOL, ARGS)
        self.assertEqual(inner.calls, [])


if __name__ == "__main__":
    unittest.main()
