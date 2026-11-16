"""Shared fixtures and fakes for verifier orchestration tests."""

from __future__ import annotations

from pathlib import Path

from claim_agent.agent.verification.interface import (
    VerifierRequest,
    VerifierResponse,
)
from claim_agent.contracts import (
    ValidationOutcome,
    ValidationResult,
    VerifierInput,
)

FIXTURE_DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "contracts"
    / "verifier"
)
REQUEST_ID = "run-005097-01:verifier:1"


def load_verifier_input() -> VerifierInput:
    """Load the frozen proposed decision and same-run evidence bundle."""

    return VerifierInput.model_validate_json(
        (FIXTURE_DIRECTORY / "valid_agree_input.json").read_text(
            encoding="utf-8"
        )
    )


def load_output(name: str) -> str:
    """Load one raw verifier response fixture."""

    return (FIXTURE_DIRECTORY / name).read_text(encoding="utf-8")


def accepted_validation(
    verifier_input: VerifierInput,
) -> ValidationResult:
    """Build the accepted deterministic result required for verification."""

    decision = verifier_input.proposed_decision
    return ValidationResult(
        decision_id=decision.decision_id,
        claim_id=decision.claim_id,
        service_line_id=decision.service_line_id,
        outcome=ValidationOutcome.ACCEPTED,
        validated_decision=decision,
    )


class FakeVerifierClient:
    """Return one response or raise one configured boundary error."""

    def __init__(
        self,
        *,
        response_text: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response_text = response_text
        self.error = error
        self.received_requests: list[VerifierRequest] = []

    def verify(self, request: VerifierRequest) -> VerifierResponse:
        """Record the request before returning or raising."""

        self.received_requests.append(request)

        if self.error is not None:
            raise self.error

        if self.response_text is None:
            raise AssertionError("Fake verifier requires output or an error")

        return VerifierResponse(
            request_id=request.request_id,
            response_text=self.response_text,
            provider_request_id="fake-provider-request",
            input_token_count=200,
            output_token_count=30,
        )


class StepClock:
    """Return deterministic timestamps for latency assertions."""

    def __init__(self, *values: float) -> None:
        self._values = list(values)

    def __call__(self) -> float:
        if not self._values:
            raise AssertionError("The test clock has no values remaining")
        return self._values.pop(0)
