"""Tests for eligibility evidence retrieval (#56)."""

import glob
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from claim_agent.contracts import ErrorCode, ToolResult, ToolStatus
from claim_agent.tools.eligibility import (
    ELIGIBILITY_ROOT,
    ELIGIBILITY_TOOL,
    EligibilityArgumentMismatchError,
    EligibilityToolDispatcher,
    find_eligibility_record,
)
from claim_agent.tools.knowledge_store import KnowledgeStore
from claim_agent.tools.snapshot_freeze import check_lock

REPO = Path(__file__).resolve().parents[2]
ACCOUNT = "PA-SYN-10104"


class Inner:
    def __init__(self):
        self.calls = []

    def dispatch(self, request_id, tool_name, tool_arguments):
        self.calls.append(tool_name)
        return ToolResult(
            request_id=request_id,
            tool_name=tool_name,
            status=ToolStatus.ERROR,
            error_code=ErrorCode.NO_MATCH,
        )


def dispatcher(
    root=ELIGIBILITY_ROOT,
    inner=None,
    account=ACCOUNT,
    allowed_payers=None,
):
    return EligibilityToolDispatcher(
        dispatcher=inner or Inner(),
        store=KnowledgeStore(root),
        patient_account=account,
        allowed_payers=allowed_payers,
    )


def ask(tool, payer, account=ACCOUNT):
    return tool.dispatch(
        "REQ-1", ELIGIBILITY_TOOL, {"patient_account": account, "payer": payer}
    )


class EligibilityToolTest(unittest.TestCase):
    def test_found_record_returns_periods_and_citable_passages(self):
        result = ask(dispatcher(), "UHC")

        self.assertIs(result.status, ToolStatus.OK)
        self.assertEqual(result.source_ids, ["ELIG-PA-SYN-10104-UHC@v1"])
        self.assertEqual(
            result.data["coverage_periods"],
            [
                {
                    "effective_from": "2025-01-01",
                    "effective_to": "2026-06-10",
                    "status": "TERMINATED",
                }
            ],
        )
        self.assertTrue(
            all(
                p.passage_id.startswith("ELIG-PA-SYN-10104-UHC@v1#E")
                for p in result.evidence_passages
            )
        )
        self.assertTrue(
            all(p.source_type == "eligibility_record" for p in result.evidence_passages)
        )

    def test_unapproved_payer_is_refused(self):
        tool = dispatcher(
            allowed_payers=("UHC", "CIGNA"),
        )

        self.assertIs(ask(tool, "CIGNA").status, ToolStatus.OK)

        with self.assertRaises(EligibilityArgumentMismatchError):
            ask(tool, "AETNA")

    def test_other_coverage_can_be_checked_at_another_payer(self):
        result = ask(dispatcher(), "CIGNA")
        self.assertEqual(
            result.data["coverage_periods"][0]["effective_from"], "2026-06-11"
        )

    def test_it_reports_evidence_and_never_decides(self):
        data = ask(dispatcher(), "UHC").data
        self.assertFalse({"eligible", "is_eligible", "covered", "decision"} & set(data))

    def test_no_record_is_an_explicit_no_match(self):
        self.assertIs(ask(dispatcher(), "AETNA").error_code, ErrorCode.NO_MATCH)
        self.assertIs(
            ask(dispatcher(account="PA-NONE"), "UHC", "PA-NONE").error_code,
            ErrorCode.NO_MATCH,
        )

    def test_malformed_requests_are_rejected(self):
        tool = dispatcher()
        for name, arguments in {
            "missing payer": {"patient_account": ACCOUNT},
            "extra argument": {
                "patient_account": ACCOUNT,
                "payer": "UHC",
                "service_date": "2026-06-18",
            },
            "blank payer": {"patient_account": ACCOUNT, "payer": " "},
            "number": {"patient_account": ACCOUNT, "payer": 5},
        }.items():
            with self.subTest(case=name):
                self.assertIs(
                    tool.dispatch("R", ELIGIBILITY_TOOL, arguments).error_code,
                    ErrorCode.MALFORMED_REQUEST,
                )

    def test_another_member_account_is_refused(self):
        with self.assertRaises(EligibilityArgumentMismatchError):
            ask(dispatcher(), "UHC", account="PA-SYN-10105")

    def test_other_tools_pass_through(self):
        inner = Inner()
        dispatcher(inner=inner).dispatch("R", "get_payer_profile", {"payer_id": "UHC"})
        self.assertEqual(inner.calls, ["get_payer_profile"])
        ask(dispatcher(inner=inner), "UHC")
        self.assertEqual(inner.calls, ["get_payer_profile"])


class UnavailableRecordTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "eligibility"
        shutil.copytree(ELIGIBILITY_ROOT, self.root)

    def test_listed_but_missing_record_is_unavailable(self):
        (self.root / "records" / "ELIG-PA-SYN-10104-UHC.v1.json").unlink()
        self.assertIs(
            ask(dispatcher(self.root), "UHC").error_code, ErrorCode.DOCUMENT_UNAVAILABLE
        )

    def test_two_records_for_one_member_and_payer_are_never_guessed(self):
        manifest = json.loads((self.root / "manifest.json").read_text())
        duplicate = dict(
            next(
                s
                for s in manifest["sources"]
                if s["source_id"] == "ELIG-PA-SYN-10104-UHC"
            )
        )
        duplicate["version"] = "2"
        manifest["sources"].append(duplicate)
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        self.assertIs(
            ask(dispatcher(self.root), "UHC").error_code, ErrorCode.DOCUMENT_UNAVAILABLE
        )


class EligibilityStoreTest(unittest.TestCase):
    def test_the_store_is_frozen_and_valid(self):
        self.assertEqual(KnowledgeStore(ELIGIBILITY_ROOT).snapshot_id, "eligibility-v1")
        self.assertEqual(check_lock(ELIGIBILITY_ROOT), [])

    def test_store_files_check_out_with_lf_on_every_platform(self):
        # A CRLF checkout (Windows, core.autocrlf=true) would change the hashes
        # and fail check_lock, so every locked JSON file must be pinned to LF.
        files = [
            str(p.relative_to(REPO)) for p in sorted(ELIGIBILITY_ROOT.rglob("*.json"))
        ]
        try:
            out = subprocess.run(
                ["git", "check-attr", "eol", "--", *files],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git is not available")
        self.assertEqual(len(out.splitlines()), len(files))
        for line in out.splitlines():
            self.assertTrue(line.endswith(": eol: lf"), line)

    def test_no_held_out_claim_has_a_record(self):
        held_out = {
            json.loads(Path(f).read_text())["patient_account"]
            for f in glob.glob(str(REPO / "datasets/held_out/claims/*.json"))
        }
        store = KnowledgeStore(ELIGIBILITY_ROOT)
        records = {
            e["patient_account"] for e in store.entries_of_type("eligibility_record")
        }
        self.assertEqual(held_out & records, set())

    def test_the_knowledge_corpus_is_untouched(self):
        self.assertEqual(KnowledgeStore(REPO / "knowledge").snapshot_id, "knowledge-v2")
        self.assertEqual(check_lock(REPO / "knowledge"), [])
        self.assertEqual(
            KnowledgeStore(REPO / "knowledge").entries_of_type("eligibility_record"), ()
        )

    def test_find_returns_none_for_unknown_member(self):
        self.assertIsNone(
            find_eligibility_record(KnowledgeStore(ELIGIBILITY_ROOT), "PA-NONE", "UHC")
        )


if __name__ == "__main__":
    unittest.main()
