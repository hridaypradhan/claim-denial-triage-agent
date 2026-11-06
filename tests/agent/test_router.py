import unittest
import json
import inspect
from types import SimpleNamespace
from pathlib import Path
from datetime import date
from unittest.mock import patch, MagicMock

from claim_agent.agent.router import analyze_and_route_claim
from claim_agent.contracts import RouteOutcome
from baseline.triage.claim import Claim

def load_fixture(filename: str) -> Claim:
    """Helper to load a real baseline JSON fixture."""
    path = Path("baseline/examples") / filename
    with open(path, "r") as f:
        data = json.load(f)
        
    # Safely filter out extra JSON keys
    valid_keys = inspect.signature(Claim).parameters.keys()
    filtered_data = {k: v for k, v in data.items() if k in valid_keys}
    
    # Convert date strings into real date objects for baseline math
    if "date_of_service" in filtered_data and isinstance(filtered_data["date_of_service"], str):
        filtered_data["date_of_service"] = date.fromisoformat(filtered_data["date_of_service"])
    if "remit_date" in filtered_data and isinstance(filtered_data["remit_date"], str):
        filtered_data["remit_date"] = date.fromisoformat(filtered_data["remit_date"])
    
    claim = Claim(**filtered_data)
    
    # Convert nested dictionaries into objects so the baseline can use dot-notation (e.g., denial.carc)
    if hasattr(claim, 'denials'):
        claim.denials = [SimpleNamespace(**d) if isinstance(d, dict) else d for d in claim.denials]
        
    return claim

class TestRouter(unittest.TestCase):
    
    # --- Real Fixture Tests (Baseline Boundary Coverage) ---

    def test_fixture_auth_missing(self):
        """Load test1_auth_missing.json and verify DETERMINISTIC_ONLY."""
        claim = load_fixture("test1_auth_missing.json")
        results = analyze_and_route_claim(claim, date(2026, 9, 6))
        finding, route = results[0]
        self.assertEqual(route.outcome, RouteOutcome.DETERMINISTIC_ONLY)

    def test_fixture_medical_necessity(self):
        """Load test2_medical_necessity.json and verify AGENT_REQUIRED."""
        claim = load_fixture("test2_medical_necessity.json")
        results = analyze_and_route_claim(claim, date(2026, 9, 6))
        finding, route = results[0]
        self.assertEqual(route.outcome, RouteOutcome.AGENT_REQUIRED)

    def test_fixture_contractual_and_patient(self):
        """Load test4_contractual_and_patient.json and verify multiple deterministic results."""
        claim = load_fixture("test4_contractual_and_patient.json")
        results = analyze_and_route_claim(claim, date(2026, 9, 6))
        self.assertEqual(len(results), 2)
        
        finding1, route1 = results[0]
        finding2, route2 = results[1]
        
        self.assertEqual(route1.outcome, RouteOutcome.DETERMINISTIC_ONLY)
        self.assertEqual(route2.outcome, RouteOutcome.DETERMINISTIC_ONLY)
        
        denial_codes = {finding1.denial_codes[0], finding2.denial_codes[0]}
        self.assertIn("CO-45", denial_codes)
        self.assertIn("PR-204", denial_codes)

    # --- Isolated Mocked Tests ---

    @patch('claim_agent.agent.router.triage_claim')
    def test_deterministic_only_routing(self, mock_triage):
        """Tests that standard non-evidence cases bypass the agent."""
        mock_triage.return_value = {
            "claim_id": "CLM-1",
            "primary_disposition": "BILL_PATIENT",
            "findings": [{
                "service_line": 1,
                "disposition": "BILL_PATIENT",
                "root_cause": "CONTRACTUAL",
                "carc": "45",
                "group_code": "CO",
                "rationale": "Patient responsibility."
            }]
        }
        results = analyze_and_route_claim(MagicMock(), date.today())
        finding, route = results[0]
        self.assertEqual(route.outcome, RouteOutcome.DETERMINISTIC_ONLY)
        self.assertEqual(finding.denial_codes[0], "CO-45")

    @patch('claim_agent.agent.router.triage_claim')
    def test_agent_required_routing(self, mock_triage):
        """Tests that evidence-dependent root causes correctly route to the agent."""
        mock_triage.return_value = {
            "claim_id": "CLM-2",
            "primary_disposition": "APPEAL_WITH_DOCUMENTATION",
            "findings": [{
                "service_line": 1,
                "disposition": "APPEAL_WITH_DOCUMENTATION",
                "root_cause": "MEDICAL_NECESSITY",
                "carc": "50",
                "group_code": "CO",
                "rationale": "Requires medical necessity check."
            }]
        }
        results = analyze_and_route_claim(MagicMock(), date.today())
        finding, route = results[0]
        self.assertEqual(route.outcome, RouteOutcome.AGENT_REQUIRED)
        self.assertIn("retrieve_medical_policy", route.authorized_tools)

    @patch('claim_agent.agent.router.triage_claim')
    def test_manual_review_routing(self, mock_triage):
        """Tests that warnings trigger a manual review."""
        mock_triage.return_value = {
            "claim_id": "CLM-3",
            "primary_disposition": "CORRECT_AND_RESUBMIT",
            "warnings": ["Low confidence baseline match"],
            "findings": [{
                "service_line": 1,
                "disposition": "CORRECT_AND_RESUBMIT",
                "root_cause": "UNKNOWN",
                "carc": "16",
                "rationale": "Warning triggered."
            }]
        }
        results = analyze_and_route_claim(MagicMock(), date.today())
        finding, route = results[0]
        self.assertEqual(route.outcome, RouteOutcome.MANUAL_REVIEW)

    @patch('claim_agent.agent.router.triage_claim')
    def test_multi_denial_case_preserves_identifiers(self, mock_triage):
        """Tests that multiple service lines are returned independently with preserved IDs."""
        mock_triage.return_value = {
            "claim_id": "CLM-4",
            "primary_disposition": "APPEAL_WITH_DOCUMENTATION",
            "findings": [
                {
                    "service_line": 1,
                    "disposition": "APPEAL_WITH_DOCUMENTATION",
                    "root_cause": "MEDICAL_NECESSITY",
                    "carc": "50",
                    "rationale": "Line 1"
                },
                {
                    "service_line": 2,
                    "disposition": "BILL_PATIENT",
                    "root_cause": "CONTRACTUAL",
                    "carc": "45",
                    "rationale": "Line 2"
                }
            ]
        }
        results = analyze_and_route_claim(MagicMock(), date.today())
        self.assertEqual(len(results), 2)
        
        self.assertEqual(results[0][0].service_line_id, 1)
        self.assertEqual(results[0][1].outcome, RouteOutcome.AGENT_REQUIRED)
        
        self.assertEqual(results[1][0].service_line_id, 2)
        self.assertEqual(results[1][1].outcome, RouteOutcome.DETERMINISTIC_ONLY)
        
        self.assertEqual(results[0][1].claim_id, "CLM-4")
        self.assertEqual(results[1][1].claim_id, "CLM-4")

if __name__ == '__main__':
    unittest.main()