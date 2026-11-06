"""Tests for the production decision-validation boundary.

These tests verify that model-produced JSON is translated into the shared
AgentDecision contract and that Neha's deterministic validation outcomes are
translated back into the runtime's stable internal result.
"""

from __future__ import annotations

import unittest
from datetime import date
from types import MappingProxyType

from claim_agent.agent.decision_validation_adapter import (
    DecisionValidationAdapter,
)
from claim_agent.agent.runtime_ports import (
    DecisionValidatorPort,
    OfflineValidationStatus,
)
from claim_agent.contracts import (
    BaselineFinding,
    Disposition,
    EvidencePassage,
    RootCause,
)

CLAIM_ID = "CLM-ADAPTER-001"
SERVICE_LINE_ID = 1
PASSAGE_ID = "ENC-ADAPTER-001@v1#P1"


class DecisionValidationAdapterTests(unittest.TestCase):
    """Verify translation between model JSON and shared validation contracts."""

    def setUp(self) -> None:
        """Create one consistent synthetic claim and evidence passage."""

        self.baseline_finding = BaselineFinding(
            claim_id=CLAIM_ID,
            service_line_id=SERVICE_LINE_ID,
            denial_codes=["CO-16"],
            root_cause=RootCause.CODING,
            baseline_disposition=Disposition.CORRECT_AND_RESUBMIT,
            required_evidence=["encounter_note"],
            rationale="The claim requires a coding correction.",
            filing_deadline=date(2026, 12, 31),
            deadline_status="ACTIVE",
        )
        self.retrieved_evidence = [
            EvidencePassage(
                passage_id=PASSAGE_ID,
                source_id="ENC-ADAPTER-001",
                source_type="encounter_note",
                version="1",
                text="The encounter note supports the documented service.",
            )
        ]

    def _make_adapter(self) -> DecisionValidationAdapter:
        """Create an adapter whose providers read the current test context."""

        return DecisionValidationAdapter(
            baseline_finding=self.baseline_finding,
            evidence_provider=lambda: self.retrieved_evidence,
            policy_formula_provider=lambda: None,
            as_of=date(2026, 11, 6),
        )

    def _valid_decision_payload(self) -> dict[str, object]:
        """Return model-shaped JSON for one valid corrected-claim decision."""

        return {
            "decision_id": "DEC-ADAPTER-001",
            "claim_id": CLAIM_ID,
            "service_line_id": SERVICE_LINE_ID,
            "root_cause": "CODING",
            "recommended_disposition": "CORRECT_AND_RESUBMIT",
            "actions": ["Correct the coding and resubmit the claim."],
            "criterion_assessments": [],
            "evidence_gaps": [],
            "agent_status": "COMPLETED",
            "manual_review_reasons": [],
            "rationale": "The encounter documentation supports correction.",
            "citation_ids": [PASSAGE_ID],
        }

    def test_adapter_satisfies_runtime_validator_port(self) -> None:
        """The production adapter should be usable wherever the port is expected."""

        adapter = self._make_adapter()

        self.assertIsInstance(adapter, DecisionValidatorPort)

    def test_valid_decision_is_accepted(self) -> None:
        """A schema-valid and safely grounded proposal should complete."""

        result = self._make_adapter().validate(self._valid_decision_payload())

        self.assertEqual(
            OfflineValidationStatus.ACCEPTED,
            result.status,
        )
        self.assertIsNotNone(result.validated_decision)
        self.assertEqual(
            "DEC-ADAPTER-001",
            result.validated_decision["decision_id"],
        )

    def test_read_only_runtime_payload_is_accepted(self) -> None:
        """The parser's immutable decision mapping should cross the adapter."""

        protected_payload = MappingProxyType(
            self._valid_decision_payload()
        )

        result = self._make_adapter().validate(protected_payload)

        self.assertEqual(
            OfflineValidationStatus.ACCEPTED,
            result.status,
        )

    def test_malformed_decision_is_repairable(self) -> None:
        """Missing contract fields should consume repair, not crash the runtime."""

        malformed_payload = self._valid_decision_payload()
        del malformed_payload["decision_id"]

        result = self._make_adapter().validate(malformed_payload)

        self.assertEqual(
            OfflineValidationStatus.REPAIRABLE,
            result.status,
        )
        self.assertIsNone(result.validated_decision)
        self.assertIn(
            "does not match AgentDecision",
            result.problem_messages[0],
        )

    def test_identity_mismatch_requires_manual_review(self) -> None:
        """A proposal for another claim must never be accepted or repaired."""

        mismatched_payload = self._valid_decision_payload()
        mismatched_payload["claim_id"] = "CLM-WRONG"

        result = self._make_adapter().validate(mismatched_payload)

        self.assertEqual(
            OfflineValidationStatus.MANUAL_REVIEW,
            result.status,
        )
        self.assertIsNone(result.validated_decision)
        self.assertIn(
            "identity",
            result.problem_messages[0].lower(),
        )

    def test_evidence_provider_reads_latest_same_run_evidence(self) -> None:
        """Evidence retrieved after adapter creation must still reach validation."""

        current_evidence: list[EvidencePassage] = []
        adapter = DecisionValidationAdapter(
            baseline_finding=self.baseline_finding,
            evidence_provider=lambda: current_evidence,
            policy_formula_provider=lambda: None,
            as_of=date(2026, 11, 6),
        )

        result_before_retrieval = adapter.validate(self._valid_decision_payload())

        current_evidence.extend(self.retrieved_evidence)

        result_after_retrieval = adapter.validate(self._valid_decision_payload())

        self.assertEqual(
            OfflineValidationStatus.MANUAL_REVIEW,
            result_before_retrieval.status,
        )
        self.assertEqual(
            OfflineValidationStatus.ACCEPTED,
            result_after_retrieval.status,
        )

    def test_policy_formula_provider_reads_latest_formula(self) -> None:
        """Appeal validation should use the formula selected for this run."""

        appeal_finding = self.baseline_finding.model_copy(
            update={
                "root_cause": RootCause.MEDICAL_NECESSITY,
                "baseline_disposition": Disposition.APPEAL_WITH_DOCUMENTATION,
            }
        )
        current_formula = {"value": "UNSUPPORTED_CRITERION"}

        adapter = DecisionValidationAdapter(
            baseline_finding=appeal_finding,
            evidence_provider=lambda: self.retrieved_evidence,
            policy_formula_provider=lambda: current_formula["value"],
            as_of=date(2026, 11, 6),
        )

        appeal_payload = self._valid_decision_payload()
        appeal_payload.update(
            {
                "root_cause": "MEDICAL_NECESSITY",
                "recommended_disposition": "APPEAL_WITH_DOCUMENTATION",
                "criterion_assessments": [
                    {
                        "criterion_id": "DOCUMENTED_SERVICE",
                        "status": "SUPPORTED",
                        "explanation": ("The encounter note documents the service."),
                        "citation_ids": [PASSAGE_ID],
                    }
                ],
            }
        )

        rejected_result = adapter.validate(appeal_payload)

        current_formula["value"] = "DOCUMENTED_SERVICE"
        accepted_result = adapter.validate(appeal_payload)

        self.assertEqual(
            OfflineValidationStatus.MANUAL_REVIEW,
            rejected_result.status,
        )
        self.assertEqual(
            OfflineValidationStatus.ACCEPTED,
            accepted_result.status,
        )


if __name__ == "__main__":
    unittest.main()
