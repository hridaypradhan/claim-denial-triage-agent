"""Inter-labeler agreement for the pilot labels (rubric section 8, step 3).

Compares two independent label files for the same dataset, field by field:
percent agreement and Cohen's kappa. The adjudicated file is not used here;
agreement is measured before adjudication.
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
    items: int
    agreed: int
    kappa: Optional[float]  # None when chance agreement is 1 (kappa undefined)

    @property
    def percent(self) -> float:
        return self.agreed / self.items if self.items else 0.0


def cohen_kappa(first: Sequence[str], second: Sequence[str]) -> Optional[float]:
    """Cohen's kappa for two raters over the same items."""

    if len(first) != len(second) or not first:
        raise ValueError("kappa needs two equal, non-empty rating lists")
    n = len(first)
    observed = sum(a == b for a, b in zip(first, second)) / n
    count_a, count_b = Counter(first), Counter(second)
    expected = sum(count_a[k] * count_b[k] for k in set(count_a) | set(count_b)) / (n * n)
    if expected == 1.0:
        return None
    return (observed - expected) / (1.0 - expected)


def _cases(path: Path) -> Dict[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    return {entry["case_id"]: entry for entry in raw["cases"]}


def _agreement(field: str, pairs: List[Tuple[str, str]]) -> Agreement:
    first = [a for a, _ in pairs]
    second = [b for _, b in pairs]
    return Agreement(field, len(pairs), sum(a == b for a, b in pairs), cohen_kappa(first, second))


def compare_label_files(first_path: Path, second_path: Path) -> Dict[str, object]:
    """Agreement on disposition, root cause, both together, and criterion status."""

    first, second = _cases(first_path), _cases(second_path)
    if set(first) != set(second):
        raise ValueError("label files cover different cases")
    case_ids = sorted(first)

    def pairs(key) -> List[Tuple[str, str]]:
        return [(key(first[c]), key(second[c])) for c in case_ids]

    results = [
        _agreement("disposition", pairs(lambda e: e["disposition"])),
        _agreement("root_cause", pairs(lambda e: e["root_cause"])),
        _agreement("joint", pairs(lambda e: "%s|%s" % (e["root_cause"], e["disposition"]))),
    ]

    # Criterion status, over criteria both labelers marked (rubric section 3 step 4).
    criterion_pairs: List[Tuple[str, str]] = []
    for case_id in case_ids:
        a = {c["criterion_id"]: c["status"] for c in first[case_id].get("criteria", [])}
        b = {c["criterion_id"]: c["status"] for c in second[case_id].get("criteria", [])}
        criterion_pairs += [(a[k], b[k]) for k in sorted(set(a) & set(b))]
    if criterion_pairs:
        results.append(_agreement("criterion_status", criterion_pairs))

    disagreements = [
        {
            "case_id": c,
            "first": "%s / %s" % (first[c]["root_cause"], first[c]["disposition"]),
            "second": "%s / %s" % (second[c]["root_cause"], second[c]["disposition"]),
        }
        for c in case_ids
        if (first[c]["root_cause"], first[c]["disposition"])
        != (second[c]["root_cause"], second[c]["disposition"])
    ]
    return {"cases": len(case_ids), "fields": results, "disagreements": disagreements}
