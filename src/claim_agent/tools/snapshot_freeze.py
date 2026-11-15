"""Validate and freeze the knowledge snapshot used by the final evaluation (#62).

``validate_snapshot`` checks the corpus is internally consistent: every
manifest hash matches its bytes, manifest fields match their documents, policy
dates, codes, and formulas are sound, encounter notes are claim-bound, passage
IDs are unique and well formed, and no file is left out of the manifest.

``freeze_snapshot`` assigns the final snapshot ID and writes
``SNAPSHOT_LOCK.json`` holding that ID, the manifest's SHA-256, and the
evidence that is intentionally unavailable. After the freeze, ``check_lock``
fails on any change, including a source edited together with its manifest
hash. A changed corpus needs a new snapshot ID, never a silent replacement.

    python -m claim_agent.tools.snapshot_freeze validate
    python -m claim_agent.tools.snapshot_freeze freeze --id knowledge-v2 \\
        --unavailable CLM-2026-710313:clinical_note:"Listed on the claim; intentionally absent"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from claim_agent.safety.formula_evaluator import evaluate_criteria_formula

LOCK_NAME = "SNAPSHOT_LOCK.json"
MANIFEST_NAME = "manifest.json"
DRAFT_SUFFIX = "-draft"
PASSAGE_ID = re.compile(r"^[^@#\s]+@v[^#\s]+#[A-Za-z]+\d+$")
FORMULA_TOKEN = re.compile(r"[A-Za-z_]+")
OPERATORS = {"AND", "OR"}
POLICY_FIELDS = ("payer", "procedure_codes", "effective_from", "effective_to")
ENCOUNTER_FIELDS = ("claim_id", "document_type")


class SnapshotError(ValueError):
    """Raised when a snapshot can't be frozen."""


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _date(value: object) -> Optional[date]:
    return None if value is None else date.fromisoformat(str(value))


def _overlap(first: Mapping, second: Mapping) -> bool:
    start = max(_date(first["effective_from"]), _date(second["effective_from"]))
    ends = [_date(e["effective_to"]) for e in (first, second) if e["effective_to"] is not None]
    return not ends or start <= min(ends)


def validate_snapshot(root: Path) -> List[str]:
    """Return every consistency problem in the corpus; empty means it validates."""

    problems: List[str] = []
    manifest = _load(root / MANIFEST_NAME)
    if not manifest.get("snapshot_id"):
        problems.append("manifest has no snapshot_id")

    seen_sources, seen_passages, listed_paths = set(), set(), set()
    policies = []
    for entry in manifest["sources"]:
        label = f'{entry["source_id"]}@v{entry["version"]}'
        key = (entry["source_id"], entry["version"])
        if key in seen_sources:
            problems.append(f"{label}: listed twice")
        seen_sources.add(key)

        path = root / entry["path"]
        listed_paths.add(path.resolve())
        if not path.is_file():
            problems.append(f"{label}: missing file {entry['path']}")
            continue
        if _sha256(path) != entry.get("sha256"):
            problems.append(f"{label}: sha256 does not match {entry['path']}")
        doc = _load(path)

        for field in ("source_id", "version", "source_type"):
            if doc.get(field) != entry.get(field):
                problems.append(f"{label}: manifest {field} differs from the document")
        if doc.get("synthetic") is not True or entry.get("synthetic") is not True:
            problems.append(f"{label}: not marked synthetic")

        prefix = f"{label}#"
        for passage in doc.get("passages", []):
            pid = passage.get("passage_id", "")
            if not pid.startswith(prefix) or not PASSAGE_ID.match(pid):
                problems.append(f"{label}: malformed passage ID {pid!r}")
            if pid in seen_passages:
                problems.append(f"{label}: duplicate passage ID {pid!r}")
            seen_passages.add(pid)
            if not str(passage.get("text", "")).strip():
                problems.append(f"{label}: empty passage {pid!r}")
        if not doc.get("passages"):
            problems.append(f"{label}: no passages")

        if entry["source_type"] == "medical_policy":
            policies.append(entry)
            problems += _check_policy(label, entry, doc)
        elif entry["source_type"] == "encounter_note":
            for field in ENCOUNTER_FIELDS:
                if doc.get(field) != entry.get(field) or not entry.get(field):
                    problems.append(f"{label}: encounter {field} missing or differs from the document")
            if not str(entry["source_id"]).startswith(f'ENC-{entry.get("claim_id")}-'):
                problems.append(f"{label}: source ID is not bound to claim {entry.get('claim_id')}")
            try:
                date.fromisoformat(doc.get("note_date", ""))
            except ValueError:
                problems.append(f"{label}: note_date is not an ISO date")

    for first, second in combinations(policies, 2):
        if first["payer"] == second["payer"] and set(first["procedure_codes"]) & set(second["procedure_codes"]):
            if _overlap(first, second):
                problems.append(
                    f'{first["source_id"]}@v{first["version"]} and {second["source_id"]}@v{second["version"]}: '
                    "effective dates overlap for the same payer and code"
                )

    for path in sorted(root.rglob("*.json")):
        if path.name in (MANIFEST_NAME, LOCK_NAME):
            continue
        if path.resolve() not in listed_paths:
            problems.append(f"{path.relative_to(root)}: file is not in the manifest")
    return problems


def _check_policy(label: str, entry: Mapping, doc: Mapping) -> List[str]:
    problems = []
    for field in POLICY_FIELDS:
        if doc.get(field) != entry.get(field):
            problems.append(f"{label}: manifest {field} differs from the document")
    start, end = _date(entry["effective_from"]), _date(entry["effective_to"])
    if end is not None and end < start:
        problems.append(f"{label}: effective_to is before effective_from")
    passage_ids = {p["passage_id"] for p in doc.get("passages", [])}
    criteria = {c["criterion_id"] for c in doc.get("criteria", [])}
    for criterion in doc.get("criteria", []):
        if criterion["passage_id"] not in passage_ids:
            problems.append(f"{label}: criterion {criterion['criterion_id']} cites a missing passage")
    formulas = doc.get("criteria_formulas", {})
    if set(formulas) != set(entry["procedure_codes"]):
        problems.append(f"{label}: formulas do not cover exactly its procedure codes")
    for code, formula in formulas.items():
        tokens = FORMULA_TOKEN.findall(formula)
        unknown = [t for t in tokens if t not in criteria | OPERATORS]
        if not tokens or unknown or formula.count("(") != formula.count(")"):
            problems.append(f"{label}: invalid formula for {code}: {formula!r}")
        elif not evaluate_criteria_formula(formula, criteria):
            problems.append(f"{label}: formula for {code} can never be satisfied: {formula!r}")
    return problems


def manifest_fingerprint(root: Path) -> str:
    """SHA-256 of the manifest bytes, which include every source's hash."""

    return _sha256(root / MANIFEST_NAME)


def check_unavailable(root: Path, unavailable: Sequence[Mapping[str, str]]) -> List[str]:
    """Each intentionally unavailable document must have no source in the corpus."""

    problems = []
    sources = _load(root / MANIFEST_NAME)["sources"]
    for item in unavailable:
        present = [
            s["source_id"] for s in sources
            if s.get("claim_id") == item["claim_id"] and s.get("document_type") == item["document_type"]
        ]
        if present:
            problems.append(f"{item['claim_id']} {item['document_type']} is recorded as unavailable but exists: {present}")
    return problems


def freeze_snapshot(root: Path, snapshot_id: str, unavailable: Sequence[Mapping[str, str]] = ()) -> dict:
    """Assign the final snapshot ID and write the lock. Refuses any invalid corpus."""

    if not snapshot_id or snapshot_id.endswith(DRAFT_SUFFIX):
        raise SnapshotError(f"{snapshot_id!r} is not a final snapshot ID")
    if (root / LOCK_NAME).exists():
        raise SnapshotError("the snapshot is already frozen; a changed corpus needs a new lock and ID")
    problems = validate_snapshot(root) + check_unavailable(root, unavailable)
    if problems:
        raise SnapshotError("cannot freeze an invalid snapshot:\n" + "\n".join(problems))

    manifest_path = root / MANIFEST_NAME
    text = manifest_path.read_text(encoding="utf-8")
    current = _load(manifest_path)["snapshot_id"]
    old, new = f'"snapshot_id": {json.dumps(current)}', f'"snapshot_id": {json.dumps(snapshot_id)}'
    if text.count(old) != 1:
        raise SnapshotError("could not find a single snapshot_id in the manifest")
    manifest_path.write_text(text.replace(old, new, 1), encoding="utf-8")

    lock = {
        "snapshot_id": snapshot_id,
        "manifest_sha256": manifest_fingerprint(root),
        "source_count": len(_load(manifest_path)["sources"]),
        "unavailable_evidence": [dict(item) for item in unavailable],
    }
    (root / LOCK_NAME).write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return lock


def check_lock(root: Path) -> List[str]:
    """After a freeze, report any difference from the locked snapshot."""

    lock_path = root / LOCK_NAME
    if not lock_path.exists():
        return []
    lock = _load(lock_path)
    manifest = _load(root / MANIFEST_NAME)
    problems = []
    if manifest["snapshot_id"] != lock["snapshot_id"]:
        problems.append(f"manifest snapshot_id {manifest['snapshot_id']!r} differs from the lock's {lock['snapshot_id']!r}")
    if manifest_fingerprint(root) != lock["manifest_sha256"]:
        problems.append("the manifest changed after the freeze; a changed corpus needs a new snapshot ID")
    if len(manifest["sources"]) != lock["source_count"]:
        problems.append("the number of sources changed after the freeze")
    return problems + validate_snapshot(root) + check_unavailable(root, lock["unavailable_evidence"])


def _parse_unavailable(values: Sequence[str]) -> List[Dict[str, str]]:
    items = []
    for value in values:
        claim_id, document_type, reason = value.split(":", 2)
        items.append({"claim_id": claim_id, "document_type": document_type, "reason": reason})
    return items


def main(arguments: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate or freeze the knowledge snapshot.")
    parser.add_argument("command", choices=("validate", "freeze"))
    parser.add_argument("--root", type=Path, default=Path("knowledge"))
    parser.add_argument("--id", dest="snapshot_id")
    parser.add_argument("--unavailable", action="append", default=[],
                        help="CLAIM_ID:DOCUMENT_TYPE:REASON for evidence that is intentionally absent")
    args = parser.parse_args(arguments)

    if args.command == "validate":
        problems = check_lock(args.root) if (args.root / LOCK_NAME).exists() else validate_snapshot(args.root)
        for problem in problems:
            print(problem)
        print("snapshot valid" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0

    lock = freeze_snapshot(args.root, args.snapshot_id or "", _parse_unavailable(args.unavailable))
    print(f"froze {lock['snapshot_id']}: {lock['source_count']} sources, manifest {lock['manifest_sha256'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
