"""Build a safe model context for one bounded output-repair attempt.

Rejected model output and validation problems are serialized as labelled data.
They do not replace the runtime's original system instructions or authorize
additional tools.
"""

from __future__ import annotations

import json
from typing import Tuple

# TODO(#6): Replace plain validation problem strings with the approved
# ValidationResult problem contract when issue #6 is resolved.


def build_repair_context(
    task_context: str,
    rejected_output: str,
    problem_messages: Tuple[str, ...],
) -> str:
    """Add rejected output and repair guidance to the existing task context.

    Args:
        task_context: Claim facts and evidence already available to the model.
        rejected_output: Raw output that parsing or validation rejected.
        problem_messages: Specific reasons the output was rejected.

    Returns:
        A new context that preserves the original claim information and labels
        all repair material as data.
    """

    if not task_context.strip():
        raise ValueError("task_context must contain a non-whitespace value")

    if not rejected_output.strip():
        raise ValueError("rejected_output must contain a non-whitespace value")

    if not problem_messages:
        raise ValueError("problem_messages must contain at least one problem")

    if any(not message.strip() for message in problem_messages):
        raise ValueError("problem_messages cannot contain blank values")

    repair_data = {
        "rejected_output": rejected_output,
        "problems": list(problem_messages),
    }
    repair_json = json.dumps(
        repair_data,
        sort_keys=True,
        separators=(",", ":"),
    )

    return (
        f"{task_context.rstrip()}\n\n"
        "BEGIN REPAIR DATA\n"
        f"{repair_json}\n"
        "END REPAIR DATA\n\n"
        "Return one corrected JSON runtime action. Do not add facts, "
        "citations, or tool results that are absent from the context."
    )
