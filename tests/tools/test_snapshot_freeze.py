"""Tests for snapshot validation and the post-freeze guard (#62)."""

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from claim_agent.tools.snapshot_freeze import (
    LOCK_NAME,
    SnapshotError,
    check_lock,
    freeze_snapshot,
    validate_snapshot,
)

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"
NOTE = "encounters/ENC-CLM-2026-005097-CN.v1.json"


class CorpusCopy(unittest.TestCase):
    """Each test works on its own copy of the committed corpus."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "knowledge"
        shutil.copytree(KNOWLEDGE, self.root)
        (self.root / LOCK_NAME).unlink(missing_ok=True)

    def manifest(self):
        return json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))

    def write_manifest(self, manifest):
        (self.root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    def edit_note_and_rehash(self):
        """Change a source and update its manifest hash: a silent replacement."""

        path = self.root / NOTE
        path.write_text(path.read_text(encoding="utf-8").replace("headache", "migraine", 1), encoding="utf-8")
        manifest = self.manifest()
        for source in manifest["sources"]:
            if source["path"] == NOTE:
                source["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write_manifest(manifest)


class ValidateTest(CorpusCopy):
    def test_committed_corpus_validates(self):
        self.assertEqual(validate_snapshot(KNOWLEDGE), [])

    def test_edited_source_fails_its_hash(self):
        path = self.root / NOTE
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        self.assertTrue(any("sha256" in p for p in validate_snapshot(self.root)))

    def test_file_left_out_of_the_manifest_is_reported(self):
        (self.root / "encounters" / "ENC-STRAY.v1.json").write_text("{}", encoding="utf-8")
        self.assertTrue(any("not in the manifest" in p for p in validate_snapshot(self.root)))

    def test_encounter_bound_to_the_wrong_claim_is_reported(self):
        manifest = self.manifest()
        for source in manifest["sources"]:
            if source["path"] == NOTE:
                source["claim_id"] = "CLM-OTHER"
        self.write_manifest(manifest)
        self.assertTrue(any("claim_id" in p for p in validate_snapshot(self.root)))

    def test_overlapping_policy_versions_are_reported(self):
        manifest = self.manifest()
        for source in manifest["sources"]:
            if source["source_id"] == "POL-AETNA-PT-THERAPY" and source["version"] == "1":
                source["effective_to"] = "2026-12-31"
        self.write_manifest(manifest)
        self.assertTrue(any("overlap" in p for p in validate_snapshot(self.root)))


class FreezeTest(CorpusCopy):
    def test_freeze_sets_the_id_and_locks_the_manifest(self):
        lock = freeze_snapshot(self.root, "knowledge-test-final")

        self.assertEqual(self.manifest()["snapshot_id"], "knowledge-test-final")
        self.assertEqual(lock["source_count"], len(self.manifest()["sources"]))
        self.assertEqual(check_lock(self.root), [])

    def test_draft_ids_cannot_be_frozen(self):
        with self.assertRaises(SnapshotError):
            freeze_snapshot(self.root, "knowledge-v9-draft")

    def test_invalid_corpus_cannot_be_frozen(self):
        (self.root / NOTE).write_text("{}", encoding="utf-8")
        with self.assertRaises(SnapshotError):
            freeze_snapshot(self.root, "knowledge-test-final")
        self.assertFalse((self.root / LOCK_NAME).exists())

    def test_a_frozen_snapshot_cannot_be_frozen_again(self):
        freeze_snapshot(self.root, "knowledge-test-final")
        with self.assertRaises(SnapshotError):
            freeze_snapshot(self.root, "knowledge-test-final-2")

    def test_silent_replacement_after_the_freeze_is_caught(self):
        freeze_snapshot(self.root, "knowledge-test-final")
        self.edit_note_and_rehash()

        self.assertEqual(validate_snapshot(self.root), [])  # hashes alone can't see it
        self.assertTrue(any("changed after the freeze" in p for p in check_lock(self.root)))

    def test_unavailable_evidence_must_really_be_absent(self):
        with self.assertRaises(SnapshotError):
            freeze_snapshot(self.root, "knowledge-test-final", [
                {"claim_id": "CLM-2026-005097", "document_type": "clinical_note", "reason": "test"},
            ])
        lock = freeze_snapshot(self.root, "knowledge-test-final", [
            {"claim_id": "CLM-2026-999999", "document_type": "clinical_note", "reason": "test"},
        ])
        self.assertEqual(lock["unavailable_evidence"][0]["claim_id"], "CLM-2026-999999")


class CommittedSnapshotTest(unittest.TestCase):
    def test_committed_snapshot_matches_its_lock(self):
        """Once the corpus is frozen, any later change to it fails here."""

        self.assertEqual(check_lock(KNOWLEDGE), [])


if __name__ == "__main__":
    unittest.main()
