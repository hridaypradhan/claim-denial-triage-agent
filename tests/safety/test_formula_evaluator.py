"""Tests for deterministic policy-formula evaluation."""

from __future__ import annotations

import unittest

from claim_agent.safety.formula_evaluator import (
    evaluate_criteria_formula,
)


class FormulaEvaluatorTests(unittest.TestCase):
    """Verify Boolean behavior and fail-closed malformed input handling."""

    def test_single_supported_criterion_is_true(self) -> None:
        """One supported criterion should satisfy its formula."""

        self.assertTrue(
            evaluate_criteria_formula(
                formula="A",
                supported_criteria={"A"},
            )
        )

    def test_missing_criterion_is_false(self) -> None:
        """An unsupported criterion should not satisfy its formula."""

        self.assertFalse(
            evaluate_criteria_formula(
                formula="A",
                supported_criteria=set(),
            )
        )

    def test_and_requires_both_criteria(self) -> None:
        """Every side of an AND expression must be supported."""

        self.assertTrue(
            evaluate_criteria_formula(
                formula="A AND B",
                supported_criteria={"A", "B"},
            )
        )
        self.assertFalse(
            evaluate_criteria_formula(
                formula="A AND B",
                supported_criteria={"A"},
            )
        )

    def test_and_has_higher_precedence_than_or(self) -> None:
        """A OR B AND C should be interpreted as A OR (B AND C)."""

        self.assertTrue(
            evaluate_criteria_formula(
                formula="A OR B AND C",
                supported_criteria={"A"},
            )
        )
        self.assertFalse(
            evaluate_criteria_formula(
                formula="A OR B AND C",
                supported_criteria={"B"},
            )
        )

    def test_parentheses_override_default_precedence(self) -> None:
        """Parentheses should group alternatives before applying AND."""

        formula = "A AND B AND (C OR D)"

        self.assertTrue(
            evaluate_criteria_formula(
                formula=formula,
                supported_criteria={"A", "B", "D"},
            )
        )
        self.assertFalse(
            evaluate_criteria_formula(
                formula=formula,
                supported_criteria={"A", "B"},
            )
        )

    def test_hyphenated_criterion_identifier_is_supported(self) -> None:
        """Criterion identifiers may contain the contract's hyphen syntax."""

        self.assertTrue(
            evaluate_criteria_formula(
                formula="CRITERION-1",
                supported_criteria={"CRITERION-1"},
            )
        )

    def test_malformed_formulas_fail_closed(self) -> None:
        """Invalid syntax must return False instead of executing or guessing."""

        malformed_formulas = (
            "",
            "   ",
            "A AND",
            "OR A",
            "A B",
            "A AND (B OR C",
            "A AND ()",
            "A && B",
            "A + B",
            "__import__('os')",
        )

        for malformed_formula in malformed_formulas:
            with self.subTest(formula=malformed_formula):
                self.assertFalse(
                    evaluate_criteria_formula(
                        formula=malformed_formula,
                        supported_criteria={"A", "B", "C"},
                    )
                )


if __name__ == "__main__":
    unittest.main()
