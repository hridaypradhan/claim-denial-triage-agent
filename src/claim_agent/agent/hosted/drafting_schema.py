"""Build strict structured output for grounded appeal drafting."""

from __future__ import annotations

from claim_agent.agent.hosted.schema_support import (
    require_and_close_all_objects,
)
from claim_agent.contracts import AppealDraft


def build_appeal_draft_json_schema() -> dict[str, object]:
    """Return a fresh, closed schema for the frozen AppealDraft contract."""

    schema = AppealDraft.model_json_schema(mode="validation")
    require_and_close_all_objects(schema)
    return schema


def build_appeal_draft_response_format() -> dict[str, object]:
    """Return the strict Chat Completions drafting configuration."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "appeal_draft",
            "strict": True,
            "schema": build_appeal_draft_json_schema(),
        },
    }
