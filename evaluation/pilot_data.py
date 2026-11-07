"""Load evaluation datasets and labels.

A dataset manifest lists one case per denied service line. A label file holds
one label per case, written with ``datasets/ANNOTATION_RUBRIC.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]


class DatasetError(ValueError):
    """Raised when a manifest or label file is inconsistent."""


@dataclass(frozen=True)
class Case:
    case_id: str
    claim_id: str
    service_line_id: int
    claim_path: Path
    scenario_family_id: str


@dataclass(frozen=True)
class Label:
    case_id: str
    root_cause: str
    disposition: str
    requires_manual_review: bool
    manual_review_reasons: Tuple[str, ...]
    deadline: Optional[str]
    forbidden_outcomes: Tuple[str, ...]


@dataclass(frozen=True)
class Dataset:
    name: str
    split: str
    as_of: str
    rubric_version: str
    cases: Tuple[Case, ...]


def _read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_dataset(manifest_path: Path, repo_root: Path = REPO_ROOT) -> Dataset:
    """Load a manifest and check that every case is unique and its claim exists."""

    raw = _read_json(manifest_path)
    cases: List[Case] = []
    seen = set()
    for entry in raw["cases"]:
        case = Case(
            case_id=entry["case_id"],
            claim_id=entry["claim_id"],
            service_line_id=int(entry["service_line_id"]),
            claim_path=repo_root / entry["claim_path"],
            scenario_family_id=entry["scenario_family_id"],
        )
        expected_id = "%s-L%d" % (case.claim_id, case.service_line_id)
        if case.case_id != expected_id:
            raise DatasetError("case_id %s should be %s" % (case.case_id, expected_id))
        if case.case_id in seen:
            raise DatasetError("duplicate case_id %s" % case.case_id)
        if not case.claim_path.is_file():
            raise DatasetError("claim file missing for %s: %s" % (case.case_id, case.claim_path))
        seen.add(case.case_id)
        cases.append(case)
    return Dataset(
        name=raw["dataset"],
        split=raw["split"],
        as_of=raw["as_of"],
        rubric_version=raw["rubric_version"],
        cases=tuple(cases),
    )


def load_labels(label_path: Path, dataset: Dataset) -> Dict[str, Label]:
    """Load labels and require exactly one label for every case in the dataset."""

    raw = _read_json(label_path)
    if raw["rubric_version"] != dataset.rubric_version:
        raise DatasetError(
            "label rubric %s does not match manifest rubric %s"
            % (raw["rubric_version"], dataset.rubric_version)
        )
    for field in ("dataset", "split"):
        if field in raw and raw[field] != getattr(dataset, _DATASET_ATTR[field]):
            raise DatasetError(
                "label file %s %r does not match manifest %r"
                % (field, raw[field], getattr(dataset, _DATASET_ATTR[field]))
            )
    cases_by_id = {case.case_id: case for case in dataset.cases}
    labels: Dict[str, Label] = {}
    for entry in raw["cases"]:
        if not isinstance(entry.get("requires_manual_review"), bool):
            raise DatasetError(
                "requires_manual_review must be a JSON true/false for %s" % entry.get("case_id")
            )
        case = cases_by_id.get(entry["case_id"])
        if case is not None:
            _check_label_identity(entry, case, dataset)
        label = Label(
            case_id=entry["case_id"],
            root_cause=entry["root_cause"],
            disposition=entry["disposition"],
            requires_manual_review=entry["requires_manual_review"],
            manual_review_reasons=tuple(entry["manual_review_reasons"]),
            deadline=entry["deadline"],
            forbidden_outcomes=tuple(entry["forbidden_outcomes"]),
        )
        if label.case_id in labels:
            raise DatasetError("duplicate label for %s" % label.case_id)
        if label.requires_manual_review != (label.disposition == "MANUAL_REVIEW"):
            raise DatasetError("requires_manual_review disagrees with disposition for %s" % label.case_id)
        labels[label.case_id] = label

    case_ids = {case.case_id for case in dataset.cases}
    missing = sorted(case_ids - set(labels))
    extra = sorted(set(labels) - case_ids)
    if missing or extra:
        raise DatasetError("labels do not match cases; missing=%s extra=%s" % (missing, extra))
    return labels


_DATASET_ATTR = {"dataset": "name", "split": "split", "as_of": "as_of"}


def _check_label_identity(entry: dict, case: Case, dataset: Dataset) -> None:
    """Reject a label whose identity fields point at a different case or dataset.

    Each field is checked only when the label has it.
    """

    expected = {
        "dataset": dataset.name,
        "split": dataset.split,
        "as_of": dataset.as_of,
        "claim_id": case.claim_id,
        "service_line_id": case.service_line_id,
        "scenario_family_id": case.scenario_family_id,
    }
    for field, want in expected.items():
        if field not in entry:
            continue
        got = entry[field]
        if field == "service_line_id" and (isinstance(got, bool) or not isinstance(got, int)):
            raise DatasetError("service_line_id must be an integer for %s" % case.case_id)
        if got != want:
            raise DatasetError(
                "label %s for %s is %r but the manifest says %r" % (field, case.case_id, got, want)
            )
