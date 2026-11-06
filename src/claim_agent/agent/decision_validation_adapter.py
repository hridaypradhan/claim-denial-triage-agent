"""Connect model decision JSON to the production safety validator.

The offline runtime intentionally uses a small internal validation result so
its scripted regression tests remain stable. This adapter converts model JSON
into the shared AgentDecision contract, invokes deterministic validation, and
translates the result back into the runtime's internal format.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Callable, Mapping, Optional, Sequence

from pydantic import ValidationError

from claim_agent.agent.runtime_ports import (
    OfflineValidationResult,
    OfflineValidationStatus,
)
from claim_agent.contracts import (
    AgentDecision,
    BaselineFinding,
    EvidencePassage,
    ValidationOutcome,
    ValidationResult,
)
from claim_agent.safety.validator import validate_agent_decision

EvidenceProvider = Callable[[], Sequence[EvidencePassage]]
PolicyFormulaProvider = Callable[[], Optional[str]]


class DecisionValidationAdapter:
    """Make the shared safety validator usable by the existing runtime."""

    def __init__(
        self,
        baseline_finding: BaselineFinding,
        evidence_provider: EvidenceProvider,
        policy_formula_provider: PolicyFormulaProvider,
        as_of: date,
    ) -> None:
        """Store the run-specific context needed for deterministic validation."""

        self._baseline_finding = baseline_finding
        self._evidence_provider = evidence_provider
        self._policy_formula_provider = policy_formula_provider
        self._as_of = as_of

    def validate(
        self,
        decision_payload: Mapping[str, object],
    ) -> OfflineValidationResult:
        """Validate one model proposal through the shared contract boundary."""

        try:
            proposed_decision = AgentDecision.model_validate_json(
                json.dumps(dict(decision_payload))
            )
        except (TypeError, ValueError, ValidationError) as error:
            # A structurally invalid model proposal is repairable model output,
            # not a crash in the deterministic validation boundary.
            return OfflineValidationResult(
                status=OfflineValidationStatus.REPAIRABLE,
                problem_messages=(
                    f"The proposed decision does not match AgentDecision: {error}",
                ),
            )

        shared_result = validate_agent_decision(
            decision=proposed_decision,
            finding=self._baseline_finding,
            retrieved_evidence=list(self._evidence_provider()),
            as_of=self._as_of,
            policy_formula=self._policy_formula_provider(),
        )

        return _to_runtime_validation_result(shared_result)


def _to_runtime_validation_result(
    shared_result: ValidationResult,
) -> OfflineValidationResult:
    """Translate a shared ValidationResult into the runtime's stable format."""

    status_by_outcome = {
        ValidationOutcome.ACCEPTED: OfflineValidationStatus.ACCEPTED,
        ValidationOutcome.REPAIRABLE: OfflineValidationStatus.REPAIRABLE,
        ValidationOutcome.REJECTED_MANUAL_REVIEW: (
            OfflineValidationStatus.MANUAL_REVIEW
        ),
    }

    validated_decision = None
    if shared_result.validated_decision is not None:
        validated_decision = shared_result.validated_decision.model_dump(
            mode="json"
        )

    return OfflineValidationResult(
        status=status_by_outcome[shared_result.outcome],
        problem_messages=tuple(
            problem.message for problem in shared_result.validation_problems
        ),
        validated_decision=validated_decision,
    )
