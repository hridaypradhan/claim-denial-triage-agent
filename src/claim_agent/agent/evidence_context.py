"""Build model context from structured offline tool observations.

Tool output is serialized as labelled data rather than inserted as instructions.
This preserves the security boundary between runtime instructions and untrusted
policy or encounter text.
"""

from __future__ import annotations

import json

from claim_agent.agent.runtime_ports import OfflineToolObservation

# TODO(#3): Replace OfflineToolObservation serialization with the approved
# ToolResult and EvidencePassage contract adapter.


def append_tool_observation(
    task_context: str,
    tool_name: str,
    observation: OfflineToolObservation,
) -> str:
    """Append one structured tool observation to the current model context.

    Args:
        task_context: Claim facts and observations already available.
        tool_name: Name of the read-only tool that produced the result.
        observation: Structured result returned through the temporary port.

    Returns:
        A new context string containing the original context and a clearly
        labelled JSON representation of the tool observation.
    """

    if not task_context.strip():
        raise ValueError("task_context must contain a non-whitespace value")

    if not tool_name.strip():
        raise ValueError("tool_name must contain a non-whitespace value")

    serializable_observation = {
        "tool_name": tool_name,
        "succeeded": observation.succeeded,
        "payload": dict(observation.payload),
        "source_ids": list(observation.source_ids),
        "error_code": observation.error_code,
        "retryable": observation.retryable,
    }
    observation_json = json.dumps(
        serializable_observation,
        sort_keys=True,
        separators=(",", ":"),
    )

    return (
        f"{task_context.rstrip()}\n\n"
        "BEGIN TOOL OBSERVATION DATA\n"
        f"{observation_json}\n"
        "END TOOL OBSERVATION DATA"
    )
