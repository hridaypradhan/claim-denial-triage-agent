"""Preserve structured evidence collected during one agent run.

The model-facing task context contains serialized tool data for planning.
Deterministic validation instead needs the original EvidencePassage contracts.
This store keeps those passages separate from prompt text and prevents one
passage identifier from referring to conflicting content.
"""

from __future__ import annotations

import json
from typing import Optional, Tuple

from claim_agent.agent.runtime_ports import OfflineToolObservation
from claim_agent.contracts import EvidencePassage


class RunEvidenceStore:
    """Accumulate validated evidence and policy context for one run."""

    def __init__(self) -> None:
        """Initialize an empty evidence collection."""

        self._passages_by_id: dict[str, EvidencePassage] = {}
        self._passage_order: list[str] = []
        self._policy_formula: Optional[str] = None

    @property
    def evidence_passages(self) -> Tuple[EvidencePassage, ...]:
        """Return passages in their original first-retrieved order."""

        return tuple(
            self._passages_by_id[passage_id] for passage_id in self._passage_order
        )

    @property
    def policy_formula(self) -> Optional[str]:
        """Return the policy formula retrieved during this run, if any."""

        return self._policy_formula

    def record_observation(
        self,
        observation: OfflineToolObservation,
    ) -> None:
        """Record structured data from one successful tool observation."""

        if not observation.succeeded:
            return

        serialized_passages = observation.payload.get(
            "evidence_passages",
            [],
        )
        if not isinstance(serialized_passages, list):
            raise ValueError(
                "evidence_passages must be a list in a successful observation"
            )

        for serialized_passage in serialized_passages:
            passage = self._parse_passage(serialized_passage)
            self._record_passage(passage)

        serialized_data = observation.payload.get("data", {})
        if not isinstance(serialized_data, dict):
            raise ValueError("data must be an object in a successful observation")

        policy_formula = serialized_data.get("criteria_formula")
        if policy_formula is not None:
            self._record_policy_formula(policy_formula)

    def _parse_passage(
        self,
        serialized_passage: object,
    ) -> EvidencePassage:
        """Convert serialized tool data back into its shared contract."""

        if not isinstance(serialized_passage, dict):
            raise ValueError("Each evidence passage must be a serialized object")

        return EvidencePassage.model_validate_json(json.dumps(serialized_passage))

    def _record_passage(
        self,
        passage: EvidencePassage,
    ) -> None:
        """Add a passage once while rejecting conflicting reuse of its ID."""

        existing_passage = self._passages_by_id.get(passage.passage_id)

        if existing_passage is None:
            self._passages_by_id[passage.passage_id] = passage
            self._passage_order.append(passage.passage_id)
            return

        if existing_passage != passage:
            raise ValueError(
                "One passage_id cannot refer to conflicting evidence: "
                f"{passage.passage_id}"
            )

    def _record_policy_formula(
        self,
        policy_formula: object,
    ) -> None:
        """Keep one nonblank, consistent policy formula for the run."""

        if not isinstance(policy_formula, str) or not policy_formula.strip():
            raise ValueError("criteria_formula must be a nonblank string")

        if self._policy_formula is not None and self._policy_formula != policy_formula:
            raise ValueError("A run cannot contain conflicting policy formulas")

        self._policy_formula = policy_formula
