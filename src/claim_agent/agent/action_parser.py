"""Parse raw model text into a small set of permitted runtime actions.

These internal actions are temporary runtime types, not the team's final shared
AgentDecision contract. The parser creates a strict safety boundary between
untrusted model text and executable runtime behavior.
"""

from __future__ import annotations

# TODO(#6): Validate proposed decisions through the approved AgentDecision
# contract instead of keeping decision_payload as an opaque mapping.

import json
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Optional


class ModelOutputParseError(ValueError):
    """Raised when model output cannot become a permitted runtime action."""


class RuntimeActionType(str, Enum):
    """Actions the offline runtime recognizes from model output."""

    CALL_TOOL = "call_tool"
    PROPOSE_DECISION = "propose_decision"
    REQUEST_MANUAL_REVIEW = "request_manual_review"


@dataclass(frozen=True)
class ParsedModelAction:
    """Validated internal action produced from raw model text.

    Only fields relevant to the selected action are populated. Mappings are
    copied and made read-only so later code cannot rewrite what the model
    originally proposed.
    """

    action_type: RuntimeActionType
    tool_name: Optional[str] = None
    tool_arguments: Mapping[str, object] = field(default_factory=dict)
    decision_payload: Mapping[str, object] = field(default_factory=dict)
    manual_review_reason: Optional[str] = None

    def __post_init__(self) -> None:
        """Protect model-supplied mappings from later mutation."""

        object.__setattr__(
            self,
            "tool_arguments",
            MappingProxyType(dict(self.tool_arguments)),
        )
        object.__setattr__(
            self,
            "decision_payload",
            MappingProxyType(dict(self.decision_payload)),
        )


def parse_model_action(response_text: str) -> ParsedModelAction:
    """Convert one JSON model response into a permitted runtime action.

    Args:
        response_text: Raw text returned by a fake or hosted model.

    Returns:
        A validated ParsedModelAction.

    Raises:
        ModelOutputParseError: If the text is not valid JSON or does not match
            one of the supported action shapes.
    """

    try:
        parsed_output = json.loads(response_text)
    except json.JSONDecodeError as error:
        raise ModelOutputParseError("Model output must be valid JSON.") from error

    if not isinstance(parsed_output, dict):
        raise ModelOutputParseError("Model output must be a JSON object.")

    action_name = _require_non_blank_string(
        parsed_output,
        field_name="action",
    )

    try:
        action_type = RuntimeActionType(action_name)
    except ValueError as error:
        allowed_actions = ", ".join(action.value for action in RuntimeActionType)
        raise ModelOutputParseError(
            f"Unsupported action {action_name!r}. "
            f"Allowed actions are: {allowed_actions}."
        ) from error

    if action_type is RuntimeActionType.CALL_TOOL:
        return _parse_tool_action(parsed_output)

    if action_type is RuntimeActionType.PROPOSE_DECISION:
        return _parse_decision_action(parsed_output)

    return _parse_manual_review_action(parsed_output)


def _parse_tool_action(
    parsed_output: Mapping[str, object],
) -> ParsedModelAction:
    """Validate a model request to call one runtime tool."""

    tool_name = _require_non_blank_string(
        parsed_output,
        field_name="tool_name",
    )
    tool_arguments = _require_mapping(
        parsed_output,
        field_name="tool_arguments",
    )

    return ParsedModelAction(
        action_type=RuntimeActionType.CALL_TOOL,
        tool_name=tool_name,
        tool_arguments=tool_arguments,
    )


def _parse_decision_action(
    parsed_output: Mapping[str, object],
) -> ParsedModelAction:
    """Validate a proposed decision without interpreting its shared schema."""

    decision_payload = _require_mapping(
        parsed_output,
        field_name="decision",
    )

    return ParsedModelAction(
        action_type=RuntimeActionType.PROPOSE_DECISION,
        decision_payload=decision_payload,
    )


def _parse_manual_review_action(
    parsed_output: Mapping[str, object],
) -> ParsedModelAction:
    """Validate the model's request to stop for human review."""

    manual_review_reason = _require_non_blank_string(
        parsed_output,
        field_name="reason",
    )

    return ParsedModelAction(
        action_type=RuntimeActionType.REQUEST_MANUAL_REVIEW,
        manual_review_reason=manual_review_reason,
    )


def _require_non_blank_string(
    source: Mapping[str, object],
    field_name: str,
) -> str:
    """Return a required string field after rejecting blank values."""

    value = source.get(field_name)

    if not isinstance(value, str) or not value.strip():
        raise ModelOutputParseError(f"{field_name} must be a non-blank string.")

    return value


def _require_mapping(
    source: Mapping[str, object],
    field_name: str,
) -> Mapping[str, object]:
    """Return a required JSON-object field."""

    value = source.get(field_name)

    if not isinstance(value, dict):
        raise ModelOutputParseError(f"{field_name} must be a JSON object.")

    return value
