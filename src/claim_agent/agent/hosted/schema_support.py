"""Shared helpers for strict hosted-provider JSON schemas."""

from __future__ import annotations


def require_and_close_all_objects(value: object) -> None:
    """Make every JSON-schema object closed and fully required.

    OpenAI-compatible strict structured output requires every object to reject
    additional properties and list all declared properties as required. The
    project still performs local Pydantic validation after receiving a response;
    this transformation only improves the provider-side output constraint.
    """

    if isinstance(value, dict):
        if value.get("type") == "object":
            properties = value.get("properties")
            if isinstance(properties, dict):
                value["required"] = list(properties)
            value["additionalProperties"] = False

        for nested_value in value.values():
            require_and_close_all_objects(nested_value)
        return

    if isinstance(value, list):
        for nested_value in value:
            require_and_close_all_objects(nested_value)
