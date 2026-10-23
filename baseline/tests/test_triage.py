import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from triage import load_claim, render_report, triage_claim
from triage.claim import ClaimFormatError

EXAMPLES = ROOT / "examples"
LABELS = json.loads((EXAMPLES / "labels.json").read_text(encoding="utf-8"))
AS_OF = date.fromisoformat(LABELS["as_of"])


def triage(filename):
    return triage_claim(load_claim(EXAMPLES / filename), as_of=AS_OF)


class LabelAgreement(unittest.TestCase):
    """Every example claim must land on its labelled root cause and disposition."""

    def test_all_examples_match_labels(self):
        files = sorted(p for p in EXAMPLES.glob("*.json") if p.name != "labels.json")
        self.assertEqual(len(files), len(LABELS["claims"]))

        for path in files:
            with self.subTest(claim=path.name):
                result = triage(path.name)
                expected = LABELS["claims"][result["claim_id"]]
                self.assertEqual(result["primary_root_cause"], expected["root_cause"])
                self.assertEqual(result["primary_disposition"], expected["disposition"])


class RuleBehaviour(unittest.TestCase):
    def test_auth_on_file_becomes_a_correction_not_an_appeal(self):
        result = triage("test1_auth_missing.json")
        self.assertFalse(result["findings"][0]["appealable"])
        self.assertIn("AUTH-7741289", " ".join(result["findings"][0]["required_actions"]))

    def test_medical_necessity_produces_an_appeal_letter(self):
        result = triage("test2_medical_necessity.json")
        report = render_report(result, as_of=AS_OF)
        self.assertIn("DRAFT APPEAL LETTER", report)
        self.assertIn("Provider Claim Dispute", report)
        self.assertIn("DRAFT: requires biller review", report)

    def test_rarc_identifies_the_missing_field(self):
        result = triage("test3_coding_incomplete.json")
        self.assertIn("rendering_provider_npi", result["findings"][0]["evidence_needed"])

    def test_patient_responsibility_group_overrides_write_off(self):
        result = triage("test4_contractual_and_patient.json")
        dispositions = {f["carc"]: f["disposition"] for f in result["findings"]}
        self.assertEqual(dispositions["45"], "NO_ACTION_CONTRACTUAL")
        self.assertEqual(dispositions["204"], "BILL_PATIENT")
        self.assertEqual(result["recoverable_amount"], 0.0)

    def test_timely_filing_without_proof_is_not_recoverable(self):
        result = triage("test5_timely_filing.json")
        self.assertEqual(result["recoverable_amount"], 0.0)
        self.assertEqual(result["findings"][0]["confidence"], "high")

    def test_bare_co16_is_escalated_instead_of_guessed(self):
        result = triage("test6_ambiguous.json")
        finding = result["findings"][0]
        self.assertEqual(finding["disposition"], "MANUAL_REVIEW")
        self.assertEqual(finding["confidence"], "low")
        self.assertEqual(len(result["warnings"]), 2)

    def test_expired_deadline_forces_manual_review(self):
        result = triage_claim(load_claim(EXAMPLES / "test2_medical_necessity.json"), as_of=date(2027, 6, 1))
        self.assertEqual(result["findings"][0]["disposition"], "MANUAL_REVIEW")
        self.assertLess(result["findings"][0]["days_remaining"], 0)


class InputValidation(unittest.TestCase):
    def test_missing_required_field_is_rejected(self):
        bad = EXAMPLES / "_tmp_invalid.json"
        bad.write_text(json.dumps({"claim_id": "X", "payer": "AETNA"}), encoding="utf-8")
        try:
            with self.assertRaises(ClaimFormatError):
                load_claim(bad)
        finally:
            bad.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
