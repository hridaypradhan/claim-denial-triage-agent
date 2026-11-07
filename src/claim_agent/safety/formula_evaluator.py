"""Evaluate policy-criterion formulas without executing Python code.

Supported formulas contain criterion identifiers, AND, OR, and parentheses.
AND binds more tightly than OR, matching ordinary Boolean-expression rules.
Malformed or unsupported formulas fail closed by returning False.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence, Set, Tuple

# Boolean operators must be complete words. For example, "ORB" is one
# criterion identifier and must not be split into the operator "OR" plus "B".
_TOKEN_PATTERN = re.compile(
    r"\b(?:AND|OR)\b|\(|\)|[A-Za-z0-9_-]+"
)
_OPERATOR_TOKENS = frozenset({"AND", "OR", "(", ")"})


def evaluate_criteria_formula(
    formula: str,
    supported_criteria: Set[str],
) -> bool:
    """Return whether supported criteria satisfy one policy formula.

    A malformed formula returns False. In the claim workflow, that result
    prevents automatic acceptance and sends the case toward manual review.
    """

    try:
        tokens = _tokenize(formula)
        parser = _FormulaParser(
            tokens=tokens,
            supported_criteria=supported_criteria,
        )
        return parser.parse()
    except ValueError:
        return False


def _tokenize(formula: str) -> Tuple[str, ...]:
    """Convert a formula into tokens while rejecting unknown characters."""

    if not formula or not formula.strip():
        raise ValueError("A policy formula cannot be blank.")

    tokens = []
    current_position = 0

    for match in _TOKEN_PATTERN.finditer(formula):
        skipped_text = formula[current_position : match.start()]

        if skipped_text.strip():
            raise ValueError("The policy formula contains an unsupported character.")

        tokens.append(match.group())
        current_position = match.end()

    if formula[current_position:].strip():
        raise ValueError("The policy formula contains an unsupported character.")

    if not tokens:
        raise ValueError("A policy formula must contain a criterion.")

    return tuple(tokens)


@dataclass
class _FormulaParser:
    """Parse and evaluate one validated token sequence."""

    tokens: Sequence[str]
    supported_criteria: Set[str]
    current_position: int = 0

    def parse(self) -> bool:
        """Evaluate the complete formula and reject unused trailing tokens."""

        result = self._parse_or_expression()

        if self._current_token() is not None:
            raise ValueError("The policy formula contains trailing tokens.")

        return result

    def _parse_or_expression(self) -> bool:
        """Evaluate AND expressions separated by OR operators."""

        result = self._parse_and_expression()

        while self._current_token() == "OR":
            self._advance()
            right_result = self._parse_and_expression()
            result = result or right_result

        return result

    def _parse_and_expression(self) -> bool:
        """Evaluate primary expressions separated by AND operators."""

        result = self._parse_primary_expression()

        while self._current_token() == "AND":
            self._advance()
            right_result = self._parse_primary_expression()
            result = result and right_result

        return result

    def _parse_primary_expression(self) -> bool:
        """Evaluate one criterion or one parenthesized expression."""

        current_token = self._current_token()

        if current_token is None:
            raise ValueError("The policy formula ended unexpectedly.")

        if current_token == "(":
            self._advance()
            result = self._parse_or_expression()

            if self._current_token() != ")":
                raise ValueError("The policy formula has an unmatched parenthesis.")

            self._advance()
            return result

        if current_token in _OPERATOR_TOKENS:
            raise ValueError("The policy formula expected a criterion identifier.")

        self._advance()
        return current_token in self.supported_criteria

    def _current_token(self) -> Optional[str]:
        """Return the next token without consuming it."""

        if self.current_position >= len(self.tokens):
            return None

        return self.tokens[self.current_position]

    def _advance(self) -> None:
        """Consume the current token."""

        self.current_position += 1
