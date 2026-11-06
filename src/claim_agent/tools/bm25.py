"""Deterministic BM25 scoring for the passages of one policy.

Decision 0001 fixes the parameters and text handling so rankings are identical
across runs.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import List, Sequence, Tuple

K1 = 1.5
B = 0.75

_TOKEN_PATTERN = re.compile(r"[^\W_]+")


def tokenize(text: str) -> List[str]:
    """Lowercase text and split it on non-alphanumeric characters."""

    return _TOKEN_PATTERN.findall(text.lower())


def score_documents(
    query: str,
    documents: Sequence[str],
) -> Tuple[float, ...]:
    """Return one BM25 score per document, in the order given.

    Document frequencies and average length come from these documents only.
    Repeated query terms count once.
    """

    tokenized_documents = [tokenize(document) for document in documents]
    document_count = len(tokenized_documents)
    total_length = sum(len(tokens) for tokens in tokenized_documents)

    if total_length == 0:
        return tuple(0.0 for _ in documents)

    average_length = total_length / document_count
    term_counts = [Counter(tokens) for tokens in tokenized_documents]
    # Sorted because set order varies between processes, and float addition
    # depends on order. Scores must be identical across runs.
    query_terms = sorted(set(tokenize(query)))

    inverse_frequencies = {}
    for term in query_terms:
        matching_documents = sum(term in counts for counts in term_counts)
        inverse_frequencies[term] = math.log(
            1
            + (document_count - matching_documents + 0.5)
            / (matching_documents + 0.5)
        )

    scores = []
    for tokens, counts in zip(tokenized_documents, term_counts):
        length_factor = K1 * (1 - B + B * len(tokens) / average_length)
        scores.append(
            sum(
                inverse_frequencies[term]
                * counts[term]
                * (K1 + 1)
                / (counts[term] + length_factor)
                for term in query_terms
            )
        )

    return tuple(scores)
