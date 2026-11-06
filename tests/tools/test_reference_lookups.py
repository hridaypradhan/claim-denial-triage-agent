"""Tests for the lookup_denial_code and get_payer_profile tools."""

import json
import unittest
from pathlib import Path

from claim_agent.contracts import ErrorCode, ToolResult, ToolStatus
from claim_agent.tools.dispatcher import (
    DENIAL_CODE_TOOL,
    PAYER_PROFILE_TOOL,
    ToolDispatcher,
)
from claim_agent.tools.knowledge_store import KnowledgeStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = REPOSITORY_ROOT / "knowledge"
BASELINE_DATA = REPOSITORY_ROOT / "baseline" / "data"


class ToolTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dispatcher = ToolDispatcher(KnowledgeStore(KNOWLEDGE))


class DenialCodeToolTest(ToolTestCase):
    def lookup(self, **overrides):
        arguments = {"carc": "50", "rarc": ["M127"], "group_code": "CO"}
        arguments.update(overrides)
        return self.dispatcher.dispatch("REQ-3", DENIAL_CODE_TOOL, arguments)

    def test_known_denial_returns_ok_result_with_identity(self):
        result = self.lookup()

        self.assertIs(result.status, ToolStatus.OK)
        self.assertEqual(result.request_id, "REQ-3")
        self.assertEqual(result.tool_name, DENIAL_CODE_TOOL)
        self.assertEqual(result.source_ids, ["REF-DENIAL-CODES@v1"])
        self.assertEqual(result.evidence_passages, [])

    def test_data_holds_category_and_defects(self):
        data = self.lookup(carc="16", rarc=["N290"]).data

        self.assertEqual(data["carc"]["category"], "CODING")
        self.assertEqual(data["group"]["code"], "CO")
        self.assertEqual(data["rarc"][0]["code"], "N290")
        self.assertEqual(
            data["identified_defects"],
            [data["rarc"][0]["missing_element"]],
        )
        self.assertEqual(data["unknown_codes"], [])

    def test_no_remark_codes_is_valid(self):
        data = self.lookup(rarc=[]).data

        self.assertEqual(data["rarc"], [])
        self.assertEqual(data["identified_defects"], [])

    def test_every_mapped_carc_can_be_looked_up(self):
        tables = json.loads(
            (BASELINE_DATA / "denial_codes.json").read_text(encoding="utf-8")
        )

        for carc_code in tables["carc"]:
            with self.subTest(carc=carc_code):
                self.assertIs(
                    self.lookup(carc=carc_code, rarc=[]).status,
                    ToolStatus.OK,
                )

    def test_unknown_carc_is_an_explicit_no_match(self):
        result = self.lookup(carc="999")

        self.assertIs(result.status, ToolStatus.ERROR)
        self.assertIs(result.error_code, ErrorCode.NO_MATCH)
        self.assertFalse(result.retryable)
        self.assertEqual(result.data, {})

    def test_unknown_rarc_stays_visible(self):
        data = self.lookup(rarc=["M127", "Z999"]).data

        self.assertEqual([entry["code"] for entry in data["rarc"]], ["M127"])
        self.assertEqual(data["unknown_codes"], ["RARC:Z999"])

    def test_unknown_group_stays_visible(self):
        data = self.lookup(group_code="XX").data

        self.assertIsNone(data["group"]["name"])
        self.assertEqual(data["unknown_codes"], ["GROUP:XX"])

    def test_result_survives_json_round_trip(self):
        result = self.lookup()

        self.assertEqual(
            ToolResult.model_validate_json(result.model_dump_json()),
            result,
        )

    def test_invalid_requests_are_malformed(self):
        invalid_requests = {
            "number carc": {"carc": 50},
            "blank carc": {"carc": " "},
            "blank group": {"group_code": ""},
            "rarc as text": {"rarc": "M127"},
            "number in rarc": {"rarc": [127]},
            "blank in rarc": {"rarc": [" "]},
            "extra argument": {"amount": "1450"},
        }

        for name, overrides in invalid_requests.items():
            with self.subTest(request=name):
                self.assertIs(
                    self.lookup(**overrides).error_code,
                    ErrorCode.MALFORMED_REQUEST,
                )

    def test_missing_argument_is_malformed(self):
        result = self.dispatcher.dispatch(
            "REQ-3",
            DENIAL_CODE_TOOL,
            {"carc": "50", "group_code": "CO"},
        )

        self.assertIs(result.error_code, ErrorCode.MALFORMED_REQUEST)


class PayerProfileToolTest(ToolTestCase):
    def lookup(self, payer_id="BCBS_AZ"):
        return self.dispatcher.dispatch(
            "REQ-4",
            PAYER_PROFILE_TOOL,
            {"payer_id": payer_id},
        )

    def test_known_payer_returns_ok_result_with_identity(self):
        result = self.lookup()

        self.assertIs(result.status, ToolStatus.OK)
        self.assertEqual(result.request_id, "REQ-4")
        self.assertEqual(result.tool_name, PAYER_PROFILE_TOOL)
        self.assertEqual(result.source_ids, ["REF-PAYER-RULES@v1"])
        self.assertEqual(result.evidence_passages, [])

    def test_data_holds_the_filing_rules(self):
        data = self.lookup().data

        self.assertEqual(data["appeal_window_days"], 180)
        self.assertEqual(data["corrected_claim_window_days"], 365)
        self.assertEqual(data["first_level_appeal"], "Provider Claim Dispute")

    def test_profile_reports_version_and_verification(self):
        data = self.lookup().data

        self.assertEqual(data["profile_version"], "baseline-v1")
        self.assertEqual(data["verification_status"], "ILLUSTRATIVE")

    def test_every_listed_payer_can_be_looked_up(self):
        tables = json.loads(
            (BASELINE_DATA / "payer_rules.json").read_text(encoding="utf-8")
        )

        for payer_id in tables["payers"]:
            with self.subTest(payer=payer_id):
                self.assertEqual(self.lookup(payer_id).data["payer_id"], payer_id)

    def test_unlisted_payer_gets_no_default_profile(self):
        result = self.lookup("SUNSTATE_HP")

        self.assertIs(result.status, ToolStatus.ERROR)
        self.assertIs(result.error_code, ErrorCode.NO_MATCH)
        self.assertEqual(result.data, {})

    def test_result_survives_json_round_trip(self):
        result = self.lookup()

        self.assertEqual(
            ToolResult.model_validate_json(result.model_dump_json()),
            result,
        )

    def test_invalid_requests_are_malformed(self):
        invalid_requests = {
            "blank payer": {"payer_id": " "},
            "number": {"payer_id": 7},
            "wrong name": {"payer": "BCBS_AZ"},
            "extra argument": {"payer_id": "BCBS_AZ", "as_of": "2026-09-06"},
        }

        for name, arguments in invalid_requests.items():
            with self.subTest(request=name):
                result = self.dispatcher.dispatch(
                    "REQ-4",
                    PAYER_PROFILE_TOOL,
                    arguments,
                )

                self.assertIs(result.error_code, ErrorCode.MALFORMED_REQUEST)


if __name__ == "__main__":
    unittest.main()
