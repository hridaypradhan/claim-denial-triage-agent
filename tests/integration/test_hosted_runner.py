"""Offline end-to-end tests for the hosted integrated runner."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claim_agent.agent.hosted.client import (
    HostedModelClient,
    HostedModelTransportRequest,
    HostedModelTransportResponse,
)
from claim_agent.agent.hosted.factory import (
    HostedModelConfigurationError,
)
from claim_agent.agent.hosted_runner import (
    HOSTED_DEMO_RUN_ID,
    create_hosted_run_id,
    main,
    run_hosted_demo,
)


class ManualReviewTransport:
    """Return a safe hosted-model response without network access."""

    def __init__(self) -> None:
        """Start with no provider requests."""

        self.requests: list[HostedModelTransportRequest] = []

    def generate(
        self,
        request: HostedModelTransportRequest,
    ) -> HostedModelTransportResponse:
        """Record the request and return one valid bounded action."""

        self.requests.append(request)

        return HostedModelTransportResponse(
            response_text=json.dumps(
                {
                    "action": "request_manual_review",
                    "reason": (
                        "The offline fake does not provide evidence "
                        "interpretation."
                    ),
                }
            ),
            provider_request_id="offline-provider-request-1",
            input_token_count=100,
            output_token_count=20,
        )


class HostedRunnerTests(unittest.TestCase):
    """Exercise hosted orchestration without credentials or network calls."""

    def test_live_run_ids_are_filesystem_safe_and_unique(self) -> None:
        """CLI runs should not overwrite an earlier immutable trace."""

        first_run_id = create_hosted_run_id()
        second_run_id = create_hosted_run_id()

        self.assertRegex(
            first_run_id,
            (
                r"^hosted-demo-[0-9]{8}T[0-9]{12}Z-"
                r"[0-9a-f]{32}$"
            ),
        )
        self.assertNotEqual(first_run_id, second_run_id)

    def test_cli_reports_missing_configuration_clearly(self) -> None:
        """A run that cannot start should fail without a traceback."""

        standard_output = io.StringIO()
        standard_error = io.StringIO()

        with patch(
            "claim_agent.agent.hosted_runner."
            "build_hosted_model_client",
            side_effect=HostedModelConfigurationError(
                "Hosted execution requires "
                "CLAIM_AGENT_LLM_API_KEY."
            ),
        ):
            with redirect_stdout(standard_output):
                with redirect_stderr(standard_error):
                    exit_code = main([])

        self.assertEqual(2, exit_code)
        self.assertEqual("", standard_output.getvalue())

        error_payload = json.loads(standard_error.getvalue())
        self.assertEqual(
            "hosted_configuration_error",
            error_payload["error"],
        )
        self.assertIn(
            "CLAIM_AGENT_LLM_API_KEY",
            error_payload["message"],
        )

    def test_cli_prints_successful_summary(self) -> None:
        """The CLI should serialize the safe runtime summary."""

        standard_output = io.StringIO()
        expected_summary = {
            "phase": "MANUAL_REVIEW",
            "terminal_reason": "model_requested_manual_review",
        }

        with patch(
            "claim_agent.agent.hosted_runner.run_hosted_demo",
            return_value=expected_summary,
        ) as hosted_run:
            with redirect_stdout(standard_output):
                exit_code = main(
                    [
                        "--pretty",
                        "--code-commit",
                        "test-commit",
                    ]
                )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            expected_summary,
            json.loads(standard_output.getvalue()),
        )

        call_arguments = hosted_run.call_args.kwargs
        self.assertEqual(
            "test-commit",
            call_arguments["code_commit"],
        )
        self.assertEqual(
            Path(".local-output") / "hosted-traces",
            call_arguments["trace_output_directory"],
        )
        self.assertRegex(
            call_arguments["run_id"],
            (
                r"^hosted-demo-[0-9]{8}T[0-9]{12}Z-"
                r"[0-9a-f]{32}$"
            ),
        )

    def test_offline_transport_produces_complete_audited_run(
        self,
    ) -> None:
        """An injected transport should traverse the real hosted boundary."""

        transport = ManualReviewTransport()
        model_client = HostedModelClient(
            transport=transport,
            model_name="beam-1-0",
            timeout_seconds=60.0,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            summary = run_hosted_demo(
                model_client=model_client,
                trace_output_directory=Path(temporary_directory),
                code_commit="offline-test-commit",
            )

            trace_path = Path(summary["canonical_trace_path"])
            trace_lines = [
                json.loads(line)
                for line in trace_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]

        self.assertEqual(
            HOSTED_DEMO_RUN_ID,
            summary["run_id"],
        )
        self.assertEqual(
            "MANUAL_REVIEW",
            summary["phase"],
        )
        self.assertEqual(
            "model_requested_manual_review",
            summary["terminal_reason"],
        )
        self.assertEqual(
            "beam-1-0",
            summary["model_id"],
        )
        self.assertEqual(
            1,
            summary["budget_usage"]["model_calls"],
        )
        self.assertEqual(
            0,
            summary["budget_usage"]["tool_calls"],
        )

        self.assertEqual(1, len(transport.requests))
        request = transport.requests[0]
        self.assertEqual(
            "beam-1-0",
            request.model_name,
        )
        self.assertIn(
            "current",
            request.system_instructions,
        )
        self.assertEqual(
            (
                "retrieve_medical_policy",
                "get_encounter_note",
            ),
            request.allowed_tool_names,
        )

        self.assertEqual(
            "RUN_STARTED",
            trace_lines[0]["event_type"],
        )
        self.assertEqual(
            "0.3",
            trace_lines[0]["payload"]["config"]["config_version"],
        )
        self.assertEqual(
            "none",
            trace_lines[0]["payload"]["config"][
                "model_parameters"
            ]["provider_options"]["reasoning_effort"],
        )
        self.assertEqual(
            "MODEL_RESPONDED",
            trace_lines[2]["event_type"],
        )
        self.assertEqual(
            "MANUAL_REVIEW",
            trace_lines[-1]["event_type"],
        )
        self.assertEqual(
            "REPORTED",
            trace_lines[2]["usage_status"],
        )

    def test_injected_client_does_not_require_api_key(self) -> None:
        """Offline execution must not read or require a developer key."""

        model_client = HostedModelClient(
            transport=ManualReviewTransport(),
            model_name="mimo-v2-6-pro",
            timeout_seconds=30.0,
        )

        summary = run_hosted_demo(model_client=model_client)

        self.assertEqual(
            "MANUAL_REVIEW",
            summary["phase"],
        )
