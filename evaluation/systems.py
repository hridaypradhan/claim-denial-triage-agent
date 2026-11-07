"""Compared systems for the pilot evaluation.

Each system turns one case (a denied service line) into a ``Prediction``.
Only the frozen baseline (S1) is implemented here. The agent system (S0) and
the single-call baseline (S2) need the hosted-model transport from #11.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from baseline.triage.claim import load_claim
from baseline.triage.rules import triage_claim

from evaluation.pilot_data import Case


class PredictionError(RuntimeError):
    """Raised when a system cannot produce one prediction for a case."""


@dataclass(frozen=True)
class Prediction:
    case_id: str
    system: str
    disposition: str
    root_cause: str
    deadline: Optional[str]
    approval_state: Optional[str] = None
    warnings: Tuple[str, ...] = ()
    details: Dict[str, object] = field(default_factory=dict)


BASELINE_V1 = "baseline-v1"


def predict_baseline_v1(case: Case, as_of: date) -> Prediction:
    """Run the unchanged rule-based baseline and keep the finding for this line.

    The baseline returns one finding per denial. A case is one service line,
    so a line with more than one denial is rejected rather than merged by a
    rule the rubric does not define.
    """

    claim = load_claim(case.claim_path)
    if claim.claim_id != case.claim_id:
        raise PredictionError("%s: claim file holds %s" % (case.case_id, claim.claim_id))

    result = triage_claim(claim, as_of)
    findings: List[dict] = [f for f in result["findings"] if f["service_line"] == case.service_line_id]
    if len(findings) != 1:
        raise PredictionError(
            "%s: expected one baseline finding for the line, got %d" % (case.case_id, len(findings))
        )
    finding = findings[0]
    return Prediction(
        case_id=case.case_id,
        system=BASELINE_V1,
        disposition=finding["disposition"],
        root_cause=finding["root_cause"],
        deadline=finding["deadline"],
        approval_state=None,  # baseline-v1 has no approval state; reported as n/a
        warnings=tuple(result["warnings"]),
        details={"carc": finding["carc"], "group_code": finding["group_code"],
                 "confidence": finding["confidence"]},
    )


SYSTEMS = {BASELINE_V1: predict_baseline_v1}
