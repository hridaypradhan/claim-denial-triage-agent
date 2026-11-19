"""Shared deterministic fixtures for drafting orchestration tests."""

from __future__ import annotations

from pathlib import Path

from claim_agent.agent.drafting.interface import DraftingRequest, DraftingResponse
from claim_agent.contracts import DraftingInput

FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "contracts"
    / "drafting"
)


def load_drafting_input() -> DraftingInput:
    """Load the shared authorized drafting fixture."""

    return DraftingInput.model_validate_json(
        (FIXTURE_DIR / "valid_draft_input.json").read_text(encoding="utf-8")
    )


def load_draft_output() -> str:
    """Load the shared grounded draft output as provider text."""

    return (FIXTURE_DIR / "valid_draft_output.json").read_text(encoding="utf-8")


class FakeDraftingClient:
    """Return one configured response or failure and record calls."""

    def __init__(
        self,
        *,
        response_text: str | None = None,
        error: Exception | None = None,
        response_request_id: str | None = None,
    ) -> None:
        self.response_text = response_text
        self.error = error
        self.response_request_id = response_request_id
        self.received_requests: list[DraftingRequest] = []

    def draft(self, request: DraftingRequest) -> DraftingResponse:
        self.received_requests.append(request)
        if self.error is not None:
            raise self.error
        if self.response_text is None:
            raise AssertionError("Fake drafting client requires response_text")
        return DraftingResponse(
            request_id=self.response_request_id or request.request_id,
            response_text=self.response_text,
            input_token_count=120,
            output_token_count=80,
            provider_request_id="fake-draft-provider-1",
        )


class StepClock:
    """Return deterministic monotonic values for elapsed-time assertions."""

    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)
