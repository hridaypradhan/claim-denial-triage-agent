"""Build strict structured output for the tool-less evidence verifier."""

from __future__ import annotations

from claim_agent.agent.hosted.schema_support import (
    require_and_close_all_objects,
)
from claim_agent.contracts import VerifierReport


def build_verifier_report_json_schema() -> dict[str, object]:
    """Return a fresh, closed schema for the frozen VerifierReport contract."""

    schema = VerifierReport.model_json_schema(mode="validation")
    require_and_close_all_objects(schema)
    return schema


def build_verifier_report_response_format() -> dict[str, object]:
    """Return the strict Chat Completions verifier response configuration."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "verifier_report",
            "strict": True,
            "schema": build_verifier_report_json_schema(),
        },
    }
