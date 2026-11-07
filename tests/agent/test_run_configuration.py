"""Tests for reproducible agent run-configuration serialization."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from claim_agent.agent.run_configuration import (
    AgentRunConfig,
    calculate_run_configuration_sha256,
    serialize_run_configuration,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIGURATION_VECTOR_PATH = (
    PROJECT_ROOT / "evaluation" / "examples" / "config_hash_vector.json"
)


class RunConfigurationHashTests(unittest.TestCase):
    """Verify compatibility with the accepted evaluation contract."""

    def setUp(self) -> None:
        """Load the shared example used by runtime and evaluation code."""

        self.configuration_vector = json.loads(
            CONFIGURATION_VECTOR_PATH.read_text(encoding="utf-8")
        )

    def test_typed_configuration_matches_shared_hash(self) -> None:
        """The typed runtime configuration must preserve the contract hash."""

        run_configuration = AgentRunConfig.model_validate(
            self.configuration_vector["config"]
        )

        self.assertEqual(
            calculate_run_configuration_sha256(run_configuration),
            self.configuration_vector["sha256"],
        )

    def test_unsorted_tools_are_rejected(self) -> None:
        """Tool order must be stable before the configuration is hashed."""

        configuration = dict(self.configuration_vector["config"])
        configuration["allowed_tools"] = list(reversed(configuration["allowed_tools"]))

        with self.assertRaisesRegex(
            ValueError,
            "allowed_tools must be sorted by tool_name",
        ):
            AgentRunConfig.model_validate(configuration)

    def test_duplicate_tool_names_are_rejected(self) -> None:
        """One tool name must not appear more than once in a run configuration."""

        configuration = dict(self.configuration_vector["config"])
        first_tool = configuration["allowed_tools"][0]
        configuration["allowed_tools"] = [first_tool, first_tool]

        with self.assertRaisesRegex(
            ValueError,
            "allowed_tools must not contain duplicate tool names",
        ):
            AgentRunConfig.model_validate(configuration)

    def test_serialization_matches_shared_contract_vector(self) -> None:
        """The runtime and evaluation harness must produce identical JSON."""

        canonical_json = serialize_run_configuration(
            self.configuration_vector["config"]
        )

        self.assertEqual(
            canonical_json,
            self.configuration_vector["canonical_json"],
        )

    def test_hash_matches_shared_contract_vector(self) -> None:
        """The configuration fingerprint must match the accepted example."""

        configuration_hash = calculate_run_configuration_sha256(
            self.configuration_vector["config"]
        )

        self.assertEqual(
            configuration_hash,
            self.configuration_vector["sha256"],
        )
