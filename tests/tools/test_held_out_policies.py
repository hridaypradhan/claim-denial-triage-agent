"""Checks for the held-out policies added for the final evaluation (#58).

These cover retrieval only: each policy is found for its own payer, procedure,
and service date and for nothing else. They encode no case answers.
"""

import unittest
from datetime import date
from pathlib import Path

from claim_agent.safety.formula_evaluator import evaluate_criteria_formula
from claim_agent.tools.knowledge_store import KnowledgeStore
from claim_agent.tools.policy_retrieval import PolicyRetrievalStatus, retrieve_policy_passages

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"

# (payer, procedure code, service date) -> expected "<source_id>@v<version>" and formula.
MATCHES = {
    ("UHC", "72148", date(2026, 7, 8)): ("POL-UHC-MRI-LSPINE@v1", "(A AND B) OR C"),
    ("UHC", "72158", date(2026, 4, 1)): ("POL-UHC-MRI-LSPINE@v1", "(A AND B) OR C"),
    ("CIGNA", "95810", date(2026, 5, 1)): ("POL-CIGNA-SLEEP-TESTING@v1", "A AND B"),
    ("CIGNA", "95806", date(2026, 5, 1)): ("POL-CIGNA-SLEEP-TESTING@v1", "A"),
    ("AETNA", "73721", date(2026, 6, 30)): ("POL-AETNA-MRI-KNEE@v1", "A AND B"),
    ("AETNA", "73721", date(2026, 7, 1)): ("POL-AETNA-MRI-KNEE@v2", "A AND B AND C"),
    ("MEDICARE_B", "82306", date(2026, 1, 1)): ("POL-MEDICAREB-VITD-TESTING@v1", "A AND B"),
    ("UHC", "74177", date(2026, 5, 1)): ("POL-UHC-AUTH-ADV-IMAGING@v1", "A AND B"),
    ("UHC", "70450", date(2026, 5, 1)): ("POL-UHC-AUTH-ADV-IMAGING@v1", "A AND B"),
    ("AETNA", "99212", date(2026, 5, 1)): ("POL-AETNA-EM-MOD25@v1", "A AND B"),
    ("AETNA", "99215", date(2026, 5, 1)): ("POL-AETNA-EM-MOD25@v1", "A AND B"),
    ("BCBS_AZ", "70486", date(2026, 5, 1)): ("POL-BCBSAZ-CT-SINUS@v1", "(A AND B) OR C"),
}

# Requests that must still find no policy: outside an effective date, another
# payer or procedure, and the Milestone 2 cases whose answers rely on NO_MATCH.
NO_MATCHES = {
    "lumbar MRI before 2026-04-01": ("UHC", "72148", date(2026, 3, 31)),
    "knee MRI before 2026": ("AETNA", "73721", date(2025, 12, 31)),
    "sleep study for another payer": ("AETNA", "95810", date(2026, 5, 1)),
    "auth policy for an unlisted CT": ("UHC", "70486", date(2026, 5, 1)),
    "E/M policy for another payer": ("UHC", "99214", date(2026, 5, 1)),
    "Dataset B B11 (UHC brain MRI)": ("UHC", "70553", date(2026, 6, 30)),
    "Dataset A 610110 (UHC knee MRI)": ("UHC", "73721", date(2026, 8, 1)),
}


class HeldOutPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = KnowledgeStore(KNOWLEDGE)

    def retrieve(self, payer, code, service_date):
        return retrieve_policy_passages(self.store, payer, code, service_date, "criteria")

    def test_each_policy_matches_its_payer_procedure_and_date(self):
        for (payer, code, service_date), (expected, formula) in MATCHES.items():
            with self.subTest(payer=payer, code=code, date=service_date):
                result = self.retrieve(payer, code, service_date)
                self.assertIs(result.status, PolicyRetrievalStatus.OK)
                self.assertEqual(f"{result.policy.source_id}@v{result.policy.version}", expected)
                self.assertEqual(result.policy.criteria_formula, formula)

    def test_other_requests_find_no_policy(self):
        for name, (payer, code, service_date) in NO_MATCHES.items():
            with self.subTest(case=name):
                self.assertIs(self.retrieve(payer, code, service_date).status, PolicyRetrievalStatus.NO_MATCH)

    def test_or_formulas_accept_either_branch(self):
        formula = "(A AND B) OR C"
        self.assertTrue(evaluate_criteria_formula(formula, {"C"}))
        self.assertTrue(evaluate_criteria_formula(formula, {"A", "B"}))
        self.assertFalse(evaluate_criteria_formula(formula, {"A"}))
        self.assertFalse(evaluate_criteria_formula(formula, {"B"}))

    def test_every_policy_ends_with_the_silence_rule(self):
        for entry in self.store.entries_of_type("medical_policy"):
            with self.subTest(policy=entry["source_id"], version=entry["version"]):
                last = self.store.load_source(entry)["passages"][-1]["text"]
                self.assertIn("does not establish that the criterion was not met", last)


if __name__ == "__main__":
    unittest.main()
