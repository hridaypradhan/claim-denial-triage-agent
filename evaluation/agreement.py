"""Agreement between the two independent labelers (rubric section 8).

Agreement is computed from the two original label files, before
adjudication, and reported as percent agreement and Cohen's kappa.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Agreement:
    field: str
    agreed: int
    total: int
    kappa: Optional[float]

    def text(self) -> str:
        if self.total == 0:
            return "n/a (no pairs)"
        kappa = "n/a" if self.kappa is None else "%.2f" % self.kappa
        return "%d/%d (%.0f%%), kappa %s" % (
            self.agreed,
            self.total,
            100 * self.agreed / self.total,
            kappa,
        )


def cohens_kappa(pairs: Sequence[Tuple[str, str]]) -> Optional[float]:
    """Cohen's kappa for two raters over categorical values.

    Returns ``None`` when chance agreement is 1, because kappa is undefined
    when both raters always use one and the same value.
    """

    if not pairs:
        return None
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    first = Counter(a for a, _ in pairs)
    second = Counter(b for _, b in pairs)
    expected = sum(first[v] * second[v] for v in set(first) | set(second)) / (n * n)
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def _agreement(field: str, pairs: List[Tuple[str, str]]) -> Agreement:
    return Agreement(
        field=field,
        agreed=sum(a == b for a, b in pairs),
        total=len(pairs),
        kappa=cohens_kappa(pairs),
    )


def _cases(path: Path) -> Dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {case["case_id"]: case for case in data["cases"]}


def labeler_agreement(first_path: Path, second_path: Path) -> List[Agreement]:
    """Compare two label files field by field over their shared cases."""

    first, second = _cases(first_path), _cases(second_path)
    if set(first) != set(second):
        raise ValueError("label files cover different cases")
    ids = sorted(first)

    criterion_pairs: List[Tuple[str, str]] = []
    for case_id in ids:
        a = {c["criterion_id"]: c["status"] for c in first[case_id]["criteria"]}
        b = {c["criterion_id"]: c["status"] for c in second[case_id]["criteria"]}
        criterion_pairs.extend((a[k], b[k]) for k in sorted(set(a) & set(b)))

    return [
        _agreement("disposition", [(first[i]["disposition"], second[i]["disposition"]) for i in ids]),
        _agreement("root_cause", [(first[i]["root_cause"], second[i]["root_cause"]) for i in ids]),
        _agreement(
            "manual_review_reasons",
            [
                (",".join(sorted(first[i]["manual_review_reasons"])), ",".join(sorted(second[i]["manual_review_reasons"])))
                for i in ids
            ],
        ),
        _agreement("criterion_status", criterion_pairs),
    ]
