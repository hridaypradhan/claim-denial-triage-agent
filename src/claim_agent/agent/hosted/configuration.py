"""Build reproducible run metadata for hosted model execution."""

from __future__ import annotations

from typing import Sequence

from claim_agent.agent.hosted.factory import (
    SELECTED_VOYAGER_PROFILE,
    HostedModelProfile,
)
from claim_agent.agent.planning_prompt import PLANNING_PROMPT_VERSION
from claim_agent.agent.run_configuration import (
    AgentRunConfig,
    AllowedTool,
    ModelParameters,
    ProviderModelOptions,
)
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits


def build_hosted_run_configuration(
    *,
    code_commit: str,
    knowledge_snapshot_id: str,
    allowed_tools: Sequence[AllowedTool],
    budget_limits: RuntimeBudgetLimits,
    profile: HostedModelProfile = SELECTED_VOYAGER_PROFILE,
    max_tool_retries: int = 1,
    tool_request_timeout_seconds: float = 30.0,
    runtime_seed: int = 0,
) -> AgentRunConfig:
    """Freeze every non-secret setting that can affect a hosted run."""

    sorted_tools = tuple(
        sorted(
            allowed_tools,
            key=lambda tool: tool.tool_name,
        )
    )

    return AgentRunConfig(
        config_version="0.3",
        code_commit=code_commit,
        model_provider=profile.provider_name,
        model_id=profile.model_id,
        model_parameters=ModelParameters(
            max_output_tokens=profile.max_completion_tokens,
            temperature=profile.temperature,
            top_p=None,
            provider_options=ProviderModelOptions(
                reasoning_effort=profile.reasoning_effort,
                response_format_mode=profile.response_format_mode,
                gateway_addons_enabled=not profile.disable_gateway_addons,
            ),
        ),
        prompt_version=PLANNING_PROMPT_VERSION,
        runtime_seed=runtime_seed,
        provider_seed=None,
        provider_seed_supported=False,
        max_model_calls=budget_limits.max_model_calls,
        max_tool_calls=budget_limits.max_tool_calls,
        max_repair_attempts=budget_limits.max_repair_attempts,
        max_total_steps=budget_limits.max_total_steps,
        max_tool_retries=max_tool_retries,
        max_runtime_seconds=budget_limits.max_runtime_seconds,
        model_request_timeout_seconds=profile.request_timeout_seconds,
        tool_request_timeout_seconds=tool_request_timeout_seconds,
        allowed_tools=sorted_tools,
        knowledge_snapshot_id=knowledge_snapshot_id,
    )
