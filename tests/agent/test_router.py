"""Tests for adapting frozen baseline output into routing contracts."""

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from baseline.triage.claim import Claim, load_claim
from claim_agent.agent.router import analyze_and_route_claim
from claim_agent.contracts import RouteOutcome
from claim_agent.safety.required_evidence import (
    REASON_MISSING_DECISION_CRITICAL,
)


def load_fixture(filename: str) -> Claim:
    """Load one fixture through the frozen baseline's public loader."""

    repository_root = Path(__file__).resolve().parents[2]
    fixture_path = repository_root / "baseline" / "examples" / filename

    return load_claim(fixture_path)


def load_development_fixture(filename: str) -> Claim:
    """Load one development claim through the baseline's public loader."""

    repository_root = Path(__file__).resolve().parents[2]
    fixture_path = (
        repository_root
        / "datasets"
        / "development"
        / "set_a"
        / "claims"
        / filename
    )

    return load_claim(fixture_path)


class RouterTests(unittest.TestCase):
    """Verify routes produced from real and isolated baseline results."""

    def test_fixture_auth_on_file_is_deterministic_only(self) -> None:
        """An existing authorization should remain a claim correction."""

        claim = load_fixture("test1_auth_missing.json")

        results = analyze_and_route_claim(claim, date(2026, 9, 6))

        _finding, route = results[0]
        self.assertEqual(route.outcome, RouteOutcome.DETERMINISTIC_ONLY)

    def test_fixture_medical_necessity_requires_agent(self) -> None:
        """Medical-necessity evidence should enter the agent workflow."""

        claim = load_fixture("test2_medical_necessity.json")

        results = analyze_and_route_claim(claim, date(2026, 9, 6))

        _finding, route = results[0]
        self.assertEqual(route.outcome, RouteOutcome.AGENT_REQUIRED)

    def test_missing_ncci_lookup_requires_manual_review(self) -> None:
        """A01 must not resolve a bundling correction without its lookup."""

        claim = load_development_fixture("A01_bundling_no_modifier.json")

        results = analyze_and_route_claim(claim, date(2026, 9, 6))

        finding, route = results[0]
        self.assertEqual(finding.required_evidence, ["ncci_edit_lookup"])
        self.assertEqual(route.outcome, RouteOutcome.MANUAL_REVIEW)
        self.assertEqual(route.reason_code, REASON_MISSING_DECISION_CRITICAL)
        self.assertIn("ncci_edit_lookup", route.routing_reason)
        self.assertEqual(route.authorized_tools, [])

    def test_missing_cob_evidence_requires_manual_review(self) -> None:
        """A07 must not reroute a claim without its COB evidence."""

        claim = load_development_fixture("A07_cob_no_primary_eob.json")

        results = analyze_and_route_claim(claim, date(2026, 9, 6))

        finding, route = results[0]
        self.assertEqual(
            finding.required_evidence,
            ["primary_eob", "cob_confirmation"],
        )
        self.assertEqual(route.outcome, RouteOutcome.MANUAL_REVIEW)
        self.assertEqual(route.reason_code, REASON_MISSING_DECISION_CRITICAL)
        self.assertIn("primary_eob", route.routing_reason)
        self.assertIn("cob_confirmation", route.routing_reason)
        self.assertEqual(route.authorized_tools, [])

    def test_agent_retrievable_evidence_preserves_agent_route(self) -> None:
        """A02 must retain the agent route that can retrieve its evidence."""

        claim = load_development_fixture("A02_bundling_modifier_59.json")

        results = analyze_and_route_claim(claim, date(2026, 9, 6))

        finding, route = results[0]
        self.assertTrue(finding.required_evidence)
        self.assertEqual(route.outcome, RouteOutcome.AGENT_REQUIRED)
        self.assertEqual(route.reason_code, "EVIDENCE_EVALUATION_REQUIRED")
        self.assertEqual(
            route.authorized_tools,
            ["retrieve_medical_policy", "get_encounter_note"],
        )

    def test_nonessential_evidence_preserves_deterministic_route(self) -> None:
        """A04's optional insurance-card context must not cause escalation."""

        claim = load_development_fixture(
            "A04_eligibility_alternate_coverage.json"
        )

        results = analyze_and_route_claim(claim, date(2026, 9, 6))

        finding, route = results[0]
        self.assertEqual(finding.required_evidence, ["updated_insurance_card"])
        self.assertEqual(route.outcome, RouteOutcome.DETERMINISTIC_ONLY)
        self.assertEqual(route.reason_code, "BASELINE_RESOLVED")

    def test_correction_input_preserves_deterministic_route(self) -> None:
        """A15's proposed procedure-code correction is not external evidence."""

        claim = load_development_fixture("A15_multiline_co_pr.json")

        results = analyze_and_route_claim(claim, date(2026, 9, 6))

        line_one = next(
            result
            for result in results
            if result[0].service_line_id == 1
        )
        finding, route = line_one
        self.assertEqual(finding.required_evidence, ["procedure_code"])
        self.assertEqual(route.outcome, RouteOutcome.DETERMINISTIC_ONLY)
        self.assertEqual(route.reason_code, "BASELINE_RESOLVED")

    def test_recorded_eligibility_preserves_deterministic_route(self) -> None:
        """A05's recorded eligibility check satisfies its evidence token."""

        claim = load_development_fixture("A05_eligibility_no_coverage.json")

        results = analyze_and_route_claim(claim, date(2026, 9, 6))

        finding, route = results[0]
        self.assertEqual(finding.required_evidence, ["eligibility_response"])
        self.assertEqual(route.outcome, RouteOutcome.DETERMINISTIC_ONLY)
        self.assertEqual(route.reason_code, "BASELINE_RESOLVED")

    def test_attached_filing_proof_preserves_deterministic_route(self) -> None:
        """A12's attached clearinghouse report satisfies its evidence token."""

        claim = load_development_fixture("A12_timely_filing_with_proof.json")

        results = analyze_and_route_claim(claim, date(2026, 9, 6))

        finding, route = results[0]
        self.assertEqual(
            finding.required_evidence,
            ["clearinghouse_acceptance_report"],
        )
        self.assertEqual(route.outcome, RouteOutcome.DETERMINISTIC_ONLY)
        self.assertEqual(route.reason_code, "BASELINE_RESOLVED")

    def test_fixture_multi_denial_preserves_deterministic_lines(self) -> None:
        """A multi-denial claim should retain both coded service lines."""

        claim = load_fixture("test4_contractual_and_patient.json")

        results = analyze_and_route_claim(claim, date(2026, 9, 6))

        self.assertEqual(len(results), 2)
        first_finding, first_route = results[0]
        second_finding, second_route = results[1]
        self.assertEqual(
            first_route.outcome,
            RouteOutcome.DETERMINISTIC_ONLY,
        )
        self.assertEqual(
            second_route.outcome,
            RouteOutcome.DETERMINISTIC_ONLY,
        )
        denial_codes = {
            first_finding.denial_codes[0],
            second_finding.denial_codes[0],
        }
        self.assertIn("CO-45", denial_codes)
        self.assertIn("PR-204", denial_codes)

    @patch("claim_agent.agent.router.triage_claim")
    def test_deterministic_only_routing(self, mock_triage: MagicMock) -> None:
        """A non-evidence case should bypass the agent."""

        mock_triage.return_value = {
            "claim_id": "CLM-1",
            "primary_disposition": "BILL_PATIENT",
            "findings": [
                {
                    "service_line": 1,
                    "disposition": "BILL_PATIENT",
                    "root_cause": "CONTRACTUAL",
                    "carc": "45",
                    "group_code": "CO",
                    "rationale": "Patient responsibility.",
                }
            ],
        }

        results = analyze_and_route_claim(MagicMock(), date.today())

        finding, route = results[0]
        self.assertEqual(route.outcome, RouteOutcome.DETERMINISTIC_ONLY)
        self.assertEqual(finding.denial_codes[0], "CO-45")

    @patch("claim_agent.agent.router.triage_claim")
    def test_agent_required_routing(self, mock_triage: MagicMock) -> None:
        """An evidence-dependent appeal should route to the agent."""

        mock_triage.return_value = {
            "claim_id": "CLM-2",
            "primary_disposition": "APPEAL_WITH_DOCUMENTATION",
            "findings": [
                {
                    "service_line": 1,
                    "disposition": "APPEAL_WITH_DOCUMENTATION",
                    "root_cause": "MEDICAL_NECESSITY",
                    "carc": "50",
                    "group_code": "CO",
                    "rationale": "Requires medical necessity check.",
                }
            ],
        }

        results = analyze_and_route_claim(MagicMock(), date.today())

        _finding, route = results[0]
        self.assertEqual(route.outcome, RouteOutcome.AGENT_REQUIRED)
        self.assertIn("retrieve_medical_policy", route.authorized_tools)

    @patch("claim_agent.agent.router.triage_claim")
    def test_warning_requires_manual_review(
        self,
        mock_triage: MagicMock,
    ) -> None:
        """A baseline warning should stop before entering the agent."""

        mock_triage.return_value = {
            "claim_id": "CLM-3",
            "primary_disposition": "CORRECT_AND_RESUBMIT",
            "warnings": ["Low confidence baseline match"],
            "findings": [
                {
                    "service_line": 1,
                    "disposition": "CORRECT_AND_RESUBMIT",
                    "root_cause": "UNKNOWN",
                    "carc": "16",
                    "rationale": "Warning triggered.",
                }
            ],
        }

        results = analyze_and_route_claim(MagicMock(), date.today())

        _finding, route = results[0]
        self.assertEqual(route.outcome, RouteOutcome.MANUAL_REVIEW)

    @patch("claim_agent.agent.router.triage_claim")
    def test_multi_denial_case_preserves_identifiers(
        self,
        mock_triage: MagicMock,
    ) -> None:
        """Each service line should receive an independently identified route."""

        mock_triage.return_value = {
            "claim_id": "CLM-4",
            "primary_disposition": "APPEAL_WITH_DOCUMENTATION",
            "findings": [
                {
                    "service_line": 1,
                    "disposition": "APPEAL_WITH_DOCUMENTATION",
                    "root_cause": "MEDICAL_NECESSITY",
                    "carc": "50",
                    "rationale": "Line 1",
                },
                {
                    "service_line": 2,
                    "disposition": "BILL_PATIENT",
                    "root_cause": "CONTRACTUAL",
                    "carc": "45",
                    "rationale": "Line 2",
                },
            ],
        }

        results = analyze_and_route_claim(MagicMock(), date.today())

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0].service_line_id, 1)
        self.assertEqual(
            results[0][1].outcome,
            RouteOutcome.AGENT_REQUIRED,
        )
        self.assertEqual(results[1][0].service_line_id, 2)
        self.assertEqual(
            results[1][1].outcome,
            RouteOutcome.DETERMINISTIC_ONLY,
        )
        self.assertEqual(results[0][1].claim_id, "CLM-4")
        self.assertEqual(results[1][1].claim_id, "CLM-4")


if __name__ == "__main__":
    unittest.main()
