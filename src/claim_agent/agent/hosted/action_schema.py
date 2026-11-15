"""Build strict structured-output schemas for hosted runtime actions.

The provider schema improves output reliability but does not replace the
runtime action parser, tool-request guard, dispatcher validation, or
deterministic decision validation. Every hosted response still crosses those
existing safety boundaries.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Mapping

from claim_agent.contracts import AgentDecision
from claim_agent.tools.dispatcher import (
    DENIAL_CODE_TOOL,
    ENCOUNTER_NOTE_TOOL,
    MEDICAL_POLICY_TOOL,
    PAYER_PROFILE_TOOL,
)


def _text_schema() -> dict[str, object]:
    """Return the shared non-blank text schema."""

    return {"type": "string", "minLength": 1}


_TOOL_ARGUMENT_SCHEMAS: Mapping[str, dict[str, object]] = {
    MEDICAL_POLICY_TOOL: {
        "type": "object",
        "properties": {
            "payer": _text_schema(),
            "procedure_code": _text_schema(),
            "service_date": {"type": "string", "format": "date"},
            "query": _text_schema(),
        },
        "required": ["payer", "procedure_code", "service_date", "query"],
        "additionalProperties": False,
    },
    ENCOUNTER_NOTE_TOOL: {
        "type": "object",
        "properties": {
            "claim_id": _text_schema(),
            "document_id": _text_schema(),
        },
        "required": ["claim_id", "document_id"],
        "additionalProperties": False,
    },
    DENIAL_CODE_TOOL: {
        "type": "object",
        "properties": {
            "carc": _text_schema(),
            "rarc": {"type": "array", "items": _text_schema()},
            "group_code": _text_schema(),
        },
        "required": ["carc", "rarc", "group_code"],
        "additionalProperties": False,
    },
    PAYER_PROFILE_TOOL: {
        "type": "object",
        "properties": {"payer_id": _text_schema()},
        "required": ["payer_id"],
        "additionalProperties": False,
    },
}


def build_runtime_action_json_schema(
    allowed_tool_names: tuple[str, ...],
) -> dict[str, object]:
    """Return a closed JSON Schema for one permitted runtime action."""

    normalized_tool_names = _normalize_allowed_tool_names(allowed_tool_names)
    decision_schema = deepcopy(
        AgentDecision.model_json_schema(mode="validation")
    )
    _require_and_close_all_objects(decision_schema)
    definitions = decision_schema.pop("$defs", {})

    action_schemas = [
        _build_tool_action_schema(tool_name)
        for tool_name in normalized_tool_names
    ]
    action_schemas.extend(
        [
            _build_decision_action_schema(decision_schema),
            _build_manual_review_action_schema(),
        ]
    )

    return {"$defs": definitions, "anyOf": action_schemas}


def build_runtime_action_response_format(
    allowed_tool_names: tuple[str, ...],
) -> dict[str, object]:
    """Return the Chat Completions strict-response configuration."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "runtime_action",
            "strict": True,
            "schema": build_runtime_action_json_schema(allowed_tool_names),
        },
    }


def _normalize_allowed_tool_names(
    allowed_tool_names: tuple[str, ...],
) -> tuple[str, ...]:
    """Validate and sort tool names for deterministic schema output."""

    if len(set(allowed_tool_names)) != len(allowed_tool_names):
        raise ValueError("allowed_tool_names must not contain duplicates")

    unknown_tool_names = sorted(
        set(allowed_tool_names) - set(_TOOL_ARGUMENT_SCHEMAS)
    )
    if unknown_tool_names:
        raise ValueError(
            "No runtime-action schema exists for tools: "
            + ", ".join(unknown_tool_names)
        )

    return tuple(sorted(allowed_tool_names))


def _build_tool_action_schema(tool_name: str) -> dict[str, object]:
    """Return one action schema with exact arguments for one tool."""

    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "const": "call_tool"},
            "tool_name": {"type": "string", "const": tool_name},
            "tool_arguments": deepcopy(_TOOL_ARGUMENT_SCHEMAS[tool_name]),
        },
        "required": ["action", "tool_name", "tool_arguments"],
        "additionalProperties": False,
    }


def _build_decision_action_schema(
    decision_schema: dict[str, object],
) -> dict[str, object]:
    """Wrap the complete shared decision contract as one action."""

    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "const": "propose_decision"},
            "decision": decision_schema,
        },
        "required": ["action", "decision"],
        "additionalProperties": False,
    }


def _build_manual_review_action_schema() -> dict[str, object]:
    """Return the fail-closed human-review action schema."""

    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "const": "request_manual_review"},
            "reason": _text_schema(),
        },
        "required": ["action", "reason"],
        "additionalProperties": False,
    }


def _require_and_close_all_objects(value: object) -> None:
    """Make every generated decision object closed and fully required."""

    if isinstance(value, dict):
        if value.get("type") == "object":
            properties = value.get("properties")
            if isinstance(properties, dict):
                value["required"] = list(properties)
            value["additionalProperties"] = False

        for nested_value in value.values():
            _require_and_close_all_objects(nested_value)
        return

    if isinstance(value, list):
        for nested_value in value:
            _require_and_close_all_objects(nested_value)
