"""Candidate ranking (pure)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .dedup import clean_identifier, normalize_title
from .relevance import lexical_relevance


def rank_candidates(
    candidates: Sequence[dict[str, Any]],
    *,
    expected_title: str | None = None,
    preferred_provider: str | None = None,
) -> list[dict[str, Any]]:
    """Rank candidates by relevance, title match, provider preference.

    Ordering signals (descending priority):

    1. exact title match with the expected title,
    2. lexical relevance of the query terms against the candidate title
       (token overlap, CJK-bigram aware),
    3. preferred-provider match,
    4. presence of a DOI (downloadable/resolvable),
    5. recency.
    """
    normalized_expected_title = normalize_title(expected_title)
    normalized_provider = str(preferred_provider or "").strip().lower() or None

    def _score(candidate: dict[str, Any]) -> tuple[int, float, int, int, int]:
        title = str(candidate.get("title") or "")
        normalized_title = normalize_title(title)
        exact_title = int(bool(normalized_expected_title and normalized_title == normalized_expected_title))
        relevance = round(lexical_relevance(expected_title or "", title), 4)
        provider_match = int(
            bool(normalized_provider and str(candidate.get("provider") or "").strip().lower() == normalized_provider)
        )
        has_doi = int(bool(clean_identifier(candidate.get("doi") or (candidate.get("identifiers") or {}).get("doi"))))
        year_str = str(candidate.get("year") or "")
        try:
            year = int(year_str)
        except (ValueError, TypeError):
            year = 0
        year_score = min(year, 2026) if year >= 2000 else 0
        return (exact_title, relevance, provider_match, has_doi, year_score)

    return sorted(candidates, key=_score, reverse=True)
