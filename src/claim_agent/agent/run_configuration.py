"""Typed configuration and deterministic hashing for one agent run."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProviderModelOptions(BaseModel):
    """Non-secret provider options that can alter model behavior."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reasoning_effort: str
    response_format_mode: str
    gateway_addons_enabled: bool


class ModelParameters(BaseModel):
    """Sampling parameters sent to the selected model provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_output_tokens: int
    temperature: float
    top_p: float | None
    provider_options: ProviderModelOptions | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class AllowedTool(BaseModel):
    """One versioned tool that the route permits the agent to call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str
    tool_version: str


class AgentRunConfig(BaseModel):
    """Frozen settings needed to reproduce and evaluate an agent run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    config_version: str
    code_commit: str
    model_provider: str
    model_id: str
    model_parameters: ModelParameters
    prompt_version: str
    runtime_seed: int
    provider_seed: int | None
    provider_seed_supported: bool
    max_model_calls: int
    max_tool_calls: int
    max_repair_attempts: int
    max_total_steps: int
    max_tool_retries: int
    max_runtime_seconds: float
    model_request_timeout_seconds: float
    tool_request_timeout_seconds: float
    allowed_tools: tuple[AllowedTool, ...]
    knowledge_snapshot_id: str

    @field_validator("allowed_tools")
    @classmethod
    def require_tools_sorted_by_name(
        cls,
        allowed_tools: tuple[AllowedTool, ...],
    ) -> tuple[AllowedTool, ...]:
        """Reject configurations whose tool order would produce unstable hashes."""

        tool_names = [tool.tool_name for tool in allowed_tools]
        if tool_names != sorted(tool_names):
            raise ValueError("allowed_tools must be sorted by tool_name")

        if len(tool_names) != len(set(tool_names)):
            raise ValueError("allowed_tools must not contain duplicate tool names")

        return allowed_tools


RunConfigurationInput: TypeAlias = AgentRunConfig | Mapping[str, object]


def configuration_as_mapping(
    run_configuration: RunConfigurationInput,
) -> Mapping[str, object]:
    """Return a JSON-compatible mapping for a typed or raw configuration."""

    if isinstance(run_configuration, AgentRunConfig):
        return run_configuration.model_dump(mode="json")

    return run_configuration


def serialize_run_configuration(
    run_configuration: RunConfigurationInput,
) -> str:
    """Return the exact JSON representation used for reproducibility.

    Sorting every key ensures that logically identical configurations receive
    the same representation even if their dictionaries were built in a
    different order.
    """

    return json.dumps(
        configuration_as_mapping(run_configuration),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def calculate_run_configuration_sha256(
    run_configuration: RunConfigurationInput,
) -> str:
    """Return the lowercase SHA-256 fingerprint of one run configuration."""

    canonical_json = serialize_run_configuration(run_configuration)

    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
