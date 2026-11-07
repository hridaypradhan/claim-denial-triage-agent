"""Prepare one typed baseline case for the agent runtime.

The deterministic baseline and router remain authoritative. This module checks
that both contracts describe the same service line, enforces whether the agent
may run, and builds the model context and tool allowlist from typed data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from claim_agent.contracts import (
    CONTRACT_VERSION,
    BaselineFinding,
    RouteDecision,
    RouteOutcome,
)


class AgentRunNotAuthorizedError(ValueError):
    """Raised when a deterministic route must bypass the agent."""


@dataclass(frozen=True)
class PreparedRuntimeCase:
    """Validated inputs needed to begin one agent run."""

    task_context: str
    allowed_tool_names: tuple[str, ...]


def prepare_runtime_case(
    baseline_finding: BaselineFinding,
    route_decision: RouteDecision,
) -> PreparedRuntimeCase:
    """Validate typed inputs and prepare deterministic runtime context."""

    _validate_contract_versions(
        baseline_finding=baseline_finding,
        route_decision=route_decision,
    )
    _validate_case_identity(
        baseline_finding=baseline_finding,
        route_decision=route_decision,
    )
    _require_agent_route(route_decision)
    allowed_tool_names = _validate_authorized_tools(route_decision.authorized_tools)

    case_data = {
        "baseline_finding": baseline_finding.model_dump(mode="json"),
        "route_decision": route_decision.model_dump(mode="json"),
    }
    serialized_case_data = json.dumps(
        case_data,
        sort_keys=True,
        separators=(",", ":"),
    )

    task_context = (
        "BEGIN DETERMINISTIC CASE DATA\n"
        f"{serialized_case_data}\n"
        "END DETERMINISTIC CASE DATA"
    )

    return PreparedRuntimeCase(
        task_context=task_context,
        allowed_tool_names=allowed_tool_names,
    )


def _validate_contract_versions(
    baseline_finding: BaselineFinding,
    route_decision: RouteDecision,
) -> None:
    """Require both inputs to use the runtime's supported contract version."""

    contract_versions = {
        baseline_finding.contract_version,
        route_decision.contract_version,
    }

    if contract_versions != {CONTRACT_VERSION}:
        raise ValueError(
            "BaselineFinding and RouteDecision must both use "
            f"contract version {CONTRACT_VERSION}."
        )


def _validate_case_identity(
    baseline_finding: BaselineFinding,
    route_decision: RouteDecision,
) -> None:
    """Reject a routing decision belonging to another service line."""

    if baseline_finding.claim_id != route_decision.claim_id:
        raise ValueError("claim_id mismatch between BaselineFinding and RouteDecision")

    if baseline_finding.service_line_id != route_decision.service_line_id:
        raise ValueError(
            "service_line_id mismatch between BaselineFinding " "and RouteDecision"
        )


def _require_agent_route(
    route_decision: RouteDecision,
) -> None:
    """Prevent deterministic and manual-review routes from entering the model."""

    if route_decision.outcome is not RouteOutcome.AGENT_REQUIRED:
        raise AgentRunNotAuthorizedError(
            "The agent cannot run for route outcome " f"{route_decision.outcome.value}."
        )


def _validate_authorized_tools(
    authorized_tools: list[str],
) -> tuple[str, ...]:
    """Return an immutable allowlist after rejecting unsafe values."""

    if any(not tool_name.strip() for tool_name in authorized_tools):
        raise ValueError("authorized_tools cannot contain blank tool names")

    if len(set(authorized_tools)) != len(authorized_tools):
        raise ValueError("authorized_tools cannot contain duplicate tool names")

    return tuple(authorized_tools)
