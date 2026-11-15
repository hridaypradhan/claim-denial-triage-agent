"""Structure checks for the held-out case set (#52).

These check that the cases are well formed and separate from development.
They never look at labels or system output, so they are safe to run before
the held-out freeze.
"""

import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (REPO_ROOT, REPO_ROOT / "baseline"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evaluation.pilot_data import load_dataset  # noqa: E402
from triage.claim import load_claim  # noqa: E402

HELD_OUT = REPO_ROOT / "datasets" / "held_out"
MANIFEST = HELD_OUT / "manifest.json"
DEVELOPMENT_MANIFESTS = sorted((REPO_ROOT / "datasets" / "development").glob("*/manifest.json"))
BASELINE_DATA = REPO_ROOT / "baseline" / "data"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class HeldOutCasesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = _read(MANIFEST)
        cls.dataset = load_dataset(MANIFEST)
        cls.claims = {case.claim_path: _read(case.claim_path) for case in cls.dataset.cases}

    def test_manifest_identity(self):
        self.assertEqual(self.dataset.split, "held_out")
        self.assertEqual(self.dataset.rubric_version, "0.1")
        self.assertEqual(self.dataset.as_of, "2026-10-30")

    def test_eighty_cases_in_three_review_batches(self):
        self.assertEqual(len(self.dataset.cases), 80)
        batches = Counter(entry["batch"] for entry in self.raw["cases"])
        self.assertEqual(batches, {"A": 28, "B": 27, "C": 25})

    def test_every_claim_loads_and_each_case_has_one_denial(self):
        for case in self.dataset.cases:
            with self.subTest(case=case.case_id):
                load_claim(case.claim_path)
                claim = self.claims[case.claim_path]
                self.assertEqual(claim["claim_id"], case.claim_id)
                denials = [d for d in claim["denials"] if d["service_line"] == case.service_line_id]
                self.assertEqual(len(denials), 1)
                self.assertIn(case.service_line_id, [line["line"] for line in claim["service_lines"]])

    def test_amounts_and_dates_are_consistent(self):
        for path, claim in self.claims.items():
            with self.subTest(claim=claim["claim_id"]):
                charges = sum(line["charge"] for line in claim["service_lines"])
                self.assertAlmostEqual(claim["billed_amount"], charges)
                self.assertLessEqual(claim["paid_amount"], claim["billed_amount"])
                self.assertAlmostEqual(claim["paid_amount"] + sum(d["amount"] for d in claim["denials"]), charges)
                self.assertLessEqual(claim["date_of_service"], claim["remit_date"])
                self.assertLessEqual(claim["remit_date"], self.dataset.as_of)

    def test_every_claim_file_is_in_the_manifest(self):
        on_disk = set((HELD_OUT / "claims").glob("*.json"))
        self.assertEqual(on_disk, set(self.claims))

    def test_no_overlap_with_development(self):
        dev_cases, dev_claims, dev_families = set(), set(), set()
        for manifest in DEVELOPMENT_MANIFESTS:
            for case in load_dataset(manifest).cases:
                dev_cases.add(case.case_id)
                dev_claims.add(case.claim_id)
                dev_families.add(case.scenario_family_id)
        self.assertTrue(DEVELOPMENT_MANIFESTS)
        for case in self.dataset.cases:
            with self.subTest(case=case.case_id):
                self.assertNotIn(case.case_id, dev_cases)
                self.assertNotIn(case.claim_id, dev_claims)
                self.assertNotIn(case.scenario_family_id, dev_families)
                self.assertTrue(case.scenario_family_id.startswith("H-"))
                self.assertRegex(case.claim_id, r"^CLM-2026-710[123]\d\d$")
                self.assertIn(HELD_OUT / "claims", case.claim_path.parents)

    def test_each_family_stays_in_one_batch(self):
        batches = {}
        for entry in self.raw["cases"]:
            batches.setdefault(entry["scenario_family_id"], set()).add(entry["batch"])
        self.assertEqual({family for family, seen in batches.items() if len(seen) > 1}, set())

    def test_only_the_intended_unknown_codes_and_payers(self):
        carcs = set(_read(BASELINE_DATA / "denial_codes.json")["carc"])
        payers = set(_read(BASELINE_DATA / "payer_rules.json")["payers"])
        unmapped = {d["carc"] for claim in self.claims.values() for d in claim["denials"]} - carcs
        unknown = {claim["payer"] for claim in self.claims.values()} - payers
        self.assertEqual(unmapped, {"234"})
        self.assertEqual(unknown, {"MOUNTAIN_HEALTH_PLAN"})

    def test_evidence_specs_cover_every_case(self):
        specs = (HELD_OUT / "EVIDENCE_SPECS.md").read_text(encoding="utf-8")
        specified = set(re.findall(r"^### (CLM-\S+)$", specs, flags=re.MULTILINE))
        self.assertEqual(specified, {case.case_id for case in self.dataset.cases})

    def test_file_names_do_not_reveal_answers(self):
        for path in self.claims:
            with self.subTest(path=path.name):
                self.assertRegex(path.name, r"^H[ABC]\d\d_[0-9A-Z]{5}_[a-z_]+\.json$")


if __name__ == "__main__":
    unittest.main()
