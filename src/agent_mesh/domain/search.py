"""BM25 keyword retrieval over the catalog.

Written here instead of pulling `rank-bm25` because that package is unmaintained (last release
0.2.2), untyped, and drags numpy in for sixty documents. Embeddings are a v1.1 item; BM25 keeps
the default demo offline, deterministic and free.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from agent_mesh.domain.catalog import Product
from agent_mesh.language import fold_accents

BM25_K1 = 1.5
"""Term-frequency saturation, the common Okapi BM25 default."""
BM25_B = 0.75
"""Document-length normalisation, the common Okapi BM25 default."""
MIN_SCORE = 0.5
"""Hits below this are dropped, so a weak match on one common term counts as NOT_FOUND rather
than as an answer about the wrong product."""

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "for",
        "of",
        "to",
        "with",
        "is",
        "it",
        "that",
        "do",
        "does",
        "what",
        "which",
        "how",
        "my",
        "me",
        "i",
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "de",
        "du",
        "et",
        "pour",
        "avec",
        "est",
        "ce",
        "qui",
        "que",
        "quel",
        "quelle",
        "mon",
        "ma",
        "mes",
        "je",
    ]
)


def tokenize(text: str) -> list[str]:
    """Lowercase, strip accents, drop stopwords, so 'électrique' matches 'electrique'."""
    return [t for t in _TOKEN_PATTERN.findall(fold_accents(text)) if t not in _STOPWORDS]


@dataclass(frozen=True)
class SearchHit:
    """A product and its BM25 score for one query."""

    product: Product
    score: float


class CatalogIndex:
    """Okapi BM25 over each product's bilingual search text, plus exact SKU lookup."""

    def __init__(self, products: list[Product]) -> None:
        self._products = products
        self._term_counts = [Counter(tokenize(p.search_text())) for p in products]
        self._lengths = [sum(counts.values()) for counts in self._term_counts]
        self._average_length = sum(self._lengths) / max(len(self._lengths), 1)
        document_frequency: Counter[str] = Counter()
        for counts in self._term_counts:
            document_frequency.update(counts.keys())
        total = len(self._term_counts)
        self._idf = {
            term: math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }
        self._by_sku = {p.sku: p for p in products}

    def get_by_sku(self, sku: str) -> Product | None:
        """Exact SKU lookup, case-insensitive."""
        return self._by_sku.get(sku.upper())

    def search(self, query: str, limit: int) -> list[SearchHit]:
        """Top `limit` hits scoring at least `MIN_SCORE`. An empty list is the NOT_FOUND signal."""
        terms = tokenize(query)
        hits: list[SearchHit] = []
        for product, counts, length in zip(
            self._products, self._term_counts, self._lengths, strict=True
        ):
            score = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if tf:
                    norm = tf + BM25_K1 * (1 - BM25_B + BM25_B * length / self._average_length)
                    score += self._idf[term] * tf * (BM25_K1 + 1) / norm
            if score >= MIN_SCORE:
                hits.append(SearchHit(product, score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]
