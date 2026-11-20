"""The held-out label freeze (#63), checked in code rather than only in tests.

``HELD_OUT_LABELS_LOCK.json`` records one hash per frozen label file. The hash is
taken over the parsed JSON re-serialized with sorted keys, so a file saved with
Windows or Unix line endings gives the same value, but any change to a label does
not. The final experiment's preflight calls :func:`check_label_lock` on every
held-out label file it is given.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import List, Sequence

FROZEN_STATUS = "FROZEN"


def canonical_sha256(value: object) -> str:
    """SHA-256 of ``value`` as JSON with sorted keys, no whitespace, UTF-8."""

    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rel(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def check_label_lock(
    lock_path: Path,
    label_paths: Sequence[Path],
    *,
    repo_root: Path,
    knowledge_snapshot_id: str,
) -> List[str]:
    """Problems that stop these label files from counting as the frozen set.

    Empty means every file is listed in the lock, is ``FROZEN``, has the locked
    case count, reviewer and review PR, and still hashes to the locked value, and
    the lock covers exactly these files for this knowledge snapshot.
    """

    if not lock_path.is_file():
        return [f"label lock {_rel(lock_path, repo_root)} is missing"]
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    problems: List[str] = []
    if lock.get("knowledge_snapshot_id") != knowledge_snapshot_id:
        problems.append(
            f"label lock is for snapshot {lock.get('knowledge_snapshot_id')!r}, not {knowledge_snapshot_id!r}"
        )

    entries = {entry["path"]: entry for entry in lock.get("files", [])}
    given = {_rel(path, repo_root): path for path in label_paths}
    for rel in sorted(set(entries) - set(given)):
        problems.append(f"{rel} is in the label lock but not in the plan")

    total = 0
    for rel, path in sorted(given.items()):
        entry = entries.get(rel)
        if entry is None:
            problems.append(f"{rel} is not in the label lock")
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        review = raw.get("review") or {}
        total += len(raw.get("cases", []))
        if raw.get("status") != FROZEN_STATUS:
            problems.append(f"{rel} has status {raw.get('status')!r}, not {FROZEN_STATUS!r}")
        if len(raw.get("cases", [])) != entry["cases"]:
            problems.append(f"{rel} has {len(raw.get('cases', []))} cases; the lock says {entry['cases']}")
        if review.get("reviewer") != entry["reviewer"] or review.get("pull_request") != entry["review_pull_request"]:
            problems.append(f"{rel} review record does not match the lock")
        if canonical_sha256(raw) != entry["canonical_sha256"]:
            problems.append(f"{rel} changed since it was frozen (hash does not match the lock)")
    if not problems and total != lock.get("cases"):
        problems.append(f"the label files hold {total} cases; the lock says {lock.get('cases')}")
    return problems
