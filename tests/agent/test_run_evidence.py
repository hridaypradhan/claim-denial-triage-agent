"""Tests for preserving structured evidence during one runtime run."""

from __future__ import annotations

import unittest
from datetime import date

from claim_agent.agent.run_evidence import RunEvidenceStore
from claim_agent.agent.tool_result_adapter import adapt_tool_result
from claim_agent.contracts import (
    ErrorCode,
    EvidencePassage,
    ToolResult,
    ToolStatus,
)

PASSAGE_ID = "POL-BCBSAZ-MRI-BRAIN@v1#P1"


def make_policy_passage(
    text: str = "MRI may be medically necessary for a suspected mass.",
) -> EvidencePassage:
    """Create one deterministic policy passage."""

    return EvidencePassage(
        passage_id=PASSAGE_ID,
        source_id="POL-BCBSAZ-MRI-BRAIN",
        source_type="medical_policy",
        version="1",
        effective_from=date(2026, 1, 1),
        text=text,
    )


def make_policy_result(
    passage: EvidencePassage,
    policy_formula: str = "A AND B",
) -> ToolResult:
    """Create a successful production-shaped retrieval result."""

    return ToolResult(
        request_id="run-1:tool:1",
        tool_name="retrieve_medical_policy",
        status=ToolStatus.OK,
        data={"criteria_formula": policy_formula},
        source_ids=["POL-BCBSAZ-MRI-BRAIN@v1"],
        evidence_passages=[passage],
    )


class RunEvidenceStoreTests(unittest.TestCase):
    """Verify evidence ordering, deduplication, and conflict handling."""

    def test_new_store_is_empty(self) -> None:
        """A run should begin without evidence or a policy formula."""

        store = RunEvidenceStore()

        self.assertEqual((), store.evidence_passages)
        self.assertIsNone(store.policy_formula)

    def test_successful_observation_records_passage_and_formula(self) -> None:
        """Production retrieval should populate validation context."""

        store = RunEvidenceStore()
        observation = adapt_tool_result(make_policy_result(make_policy_passage()))

        store.record_observation(observation)

        self.assertEqual(
            (PASSAGE_ID,),
            tuple(passage.passage_id for passage in store.evidence_passages),
        )
        self.assertEqual("A AND B", store.policy_formula)

    def test_repeated_identical_passage_is_stored_once(self) -> None:
        """A repeated retrieval must not duplicate the same evidence."""

        store = RunEvidenceStore()
        observation = adapt_tool_result(make_policy_result(make_policy_passage()))

        store.record_observation(observation)
        store.record_observation(observation)

        self.assertEqual(1, len(store.evidence_passages))

    def test_conflicting_passage_content_is_rejected(self) -> None:
        """The same passage ID must never identify two different texts."""

        store = RunEvidenceStore()
        first_observation = adapt_tool_result(
            make_policy_result(make_policy_passage(text="First policy text."))
        )
        conflicting_observation = adapt_tool_result(
            make_policy_result(make_policy_passage(text="Conflicting policy text."))
        )

        store.record_observation(first_observation)

        with self.assertRaisesRegex(
            ValueError,
            "conflicting evidence",
        ):
            store.record_observation(conflicting_observation)

    def test_conflicting_policy_formulas_are_rejected(self) -> None:
        """One run cannot silently change its policy decision rule."""

        store = RunEvidenceStore()
        first_observation = adapt_tool_result(
            make_policy_result(
                make_policy_passage(),
                policy_formula="A AND B",
            )
        )
        conflicting_observation = adapt_tool_result(
            make_policy_result(
                make_policy_passage(),
                policy_formula="A OR B",
            )
        )

        store.record_observation(first_observation)

        with self.assertRaisesRegex(
            ValueError,
            "conflicting policy formulas",
        ):
            store.record_observation(conflicting_observation)

    def test_failed_observation_does_not_add_evidence(self) -> None:
        """A failed lookup must not create evidence for validation."""

        store = RunEvidenceStore()
        failed_result = ToolResult(
            request_id="run-1:tool:1",
            tool_name="retrieve_medical_policy",
            status=ToolStatus.ERROR,
            error_code=ErrorCode.TIMEOUT,
            retryable=True,
        )

        store.record_observation(adapt_tool_result(failed_result))

        self.assertEqual((), store.evidence_passages)
        self.assertIsNone(store.policy_formula)


if __name__ == "__main__":
    unittest.main()
