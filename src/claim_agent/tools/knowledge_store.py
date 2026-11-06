"""Read-only access to the versioned knowledge snapshot.

Every retrieval tool loads sources through the manifest, so a document that is
not listed there cannot reach the agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Tuple


class KnowledgeStoreError(RuntimeError):
    """Raised when the knowledge snapshot cannot be read."""


class KnowledgeStore:
    """Load manifest entries and source documents from one snapshot."""

    def __init__(self, knowledge_root: Path) -> None:
        """Read the manifest found in knowledge_root."""

        self._knowledge_root = Path(knowledge_root)
        self._manifest = _read_json(self._knowledge_root / "manifest.json")

    @property
    def snapshot_id(self) -> str:
        """Return the identifier of the frozen knowledge snapshot."""

        return self._manifest["snapshot_id"]

    def entries_of_type(
        self,
        source_type: str,
    ) -> Tuple[Mapping[str, object], ...]:
        """Return the manifest entries for one source type."""

        return tuple(
            entry
            for entry in self._manifest["sources"]
            if entry["source_type"] == source_type
        )

    def load_source(
        self,
        manifest_entry: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Return the source document named by one manifest entry."""

        return _read_json(self._knowledge_root / str(manifest_entry["path"]))


def _read_json(path: Path) -> Mapping[str, object]:
    """Read one JSON file, reporting any failure as a store error."""

    try:
        with open(path, encoding="utf-8") as json_file:
            return json.load(json_file)
    except (OSError, ValueError) as error:
        raise KnowledgeStoreError(f"Cannot read {path.name}.") from error
