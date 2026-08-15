"""Multilingual provider planning and search orchestration.

search_multilingual calls gateway directly (not workflow) for search operations.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any, TypedDict

from .gateway import search_provider
from .normalizers import normalize_items
from .provider_health import get_health_tracker
from .rerank import rerank_enabled


class ProviderPlanItem(TypedDict):
    route: str
    provider: str


LANG_PROVIDER_MATRIX: dict[str, list[ProviderPlanItem]] = {
    "zh": [
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "openalex"},
        {"route": "api", "provider": "doaj"},
        {"route": "api", "provider": "pmc"},
    ],
    "ja": [
        {"route": "api", "provider": "jstage"},
        {"route": "api", "provider": "cinii"},
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "doaj"},
        {"route": "api", "provider": "pmc"},
    ],
    "ko": [
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "doaj"},
    ],
    "es": [
        {"route": "api", "provider": "scielo"},
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "unpaywall"},
    ],
    "pt": [
        {"route": "api", "provider": "scielo"},
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "unpaywall"},
    ],
    "en": [
        {"route": "api", "provider": "pmc"},
        {"route": "api", "provider": "europepmc"},
        {"route": "api", "provider": "pubmed"},
        {"route": "api", "provider": "semantic_scholar"},
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "arxiv"},
        {"route": "api", "provider": "biorxiv"},
        {"route": "api", "provider": "medrxiv"},
        {"route": "api", "provider": "openalex"},
        {"route": "api", "provider": "openaire"},
        {"route": "api", "provider": "base"},
        {"route": "api", "provider": "core"},
        {"route": "api", "provider": "clinical_trials"},
        {"route": "api", "provider": "zenodo"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "doaj"},
    ],
    "de": [
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "europepmc"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "openalex"},
        {"route": "api", "provider": "base"},
        {"route": "api", "provider": "doaj"},
    ],
    "fr": [
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "europepmc"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "openalex"},
        {"route": "api", "provider": "doaj"},
        {"route": "api", "provider": "pmc"},
    ],
    "ru": [
        {"route": "api", "provider": "pmc"},
        {"route": "api", "provider": "europepmc"},
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "openalex"},
    ],
    "auto": [
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "semantic_scholar"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "openalex"},
        {"route": "api", "provider": "europepmc"},
        {"route": "api", "provider": "clinical_trials"},
        {"route": "api", "provider": "doaj"},
        {"route": "api", "provider": "pmc"},
        {"route": "api", "provider": "pubmed"},
        {"route": "api", "provider": "zenodo"},
    ],
}

_TITLE_NORMALIZER = re.compile(r"[^\w一-鿿぀-ヿ가-힯]+", re.UNICODE)


def build_provider_plan(
    *,
    language: str = "auto",
    provider_hints: Sequence[str] | None = None,
) -> list[ProviderPlanItem]:
    """Build a provider execution plan based on language and hints."""
    normalized_language = (language or "auto").strip().lower() or "auto"
    plan = list(LANG_PROVIDER_MATRIX.get(normalized_language, LANG_PROVIDER_MATRIX["auto"]))
    hints = [str(item).strip().lower() for item in (provider_hints or []) if str(item).strip()]
    if not hints:
        return plan
    hinted = [item for item in plan if item["provider"] in hints]
    remaining = [item for item in plan if item["provider"] not in hints]
    return hinted + remaining


def _normalize_title(title: str | None) -> str | None:
    if not title:
        return None
    normalized = _TITLE_NORMALIZER.sub("", str(title).casefold())
    return normalized or None


def _clean_identifier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.casefold() or None


def _candidate_keys(candidate: dict[str, Any]) -> list[tuple[str, str]]:
    identifiers = candidate.get("identifiers") or {}
    keys: list[tuple[str, str]] = []
    doi = _clean_identifier(candidate.get("doi") or identifiers.get("doi"))
    if doi:
        keys.append(("doi", doi))
    url = _clean_identifier(
        candidate.get("url") or candidate.get("detail_link") or identifiers.get("url") or identifiers.get("detail_link")
    )
    if url:
        keys.append(("url", url))
    normalized_title = _normalize_title(candidate.get("title"))
    if normalized_title:
        keys.append(("title", normalized_title))
    return keys


def dedupe_candidates(candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate candidates by DOI, URL, or title."""
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        keys = _candidate_keys(candidate)
        if keys and any(key in seen for key in keys):
            continue
        deduped.append(candidate)
        for key in keys:
            seen.add(key)
    return deduped


def _build_candidate_id(candidate: dict[str, Any]) -> str:
    identity = {
        "provider": candidate.get("provider"),
        "route": candidate.get("route"),
        "doi": candidate.get("doi"),
        "url": candidate.get("url"),
        "title": candidate.get("title"),
    }
    digest = hashlib.sha1(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"cand-{digest[:12]}"


def _normalize_candidate(item: dict[str, Any], plan_item: ProviderPlanItem) -> dict[str, Any]:
    identifiers = dict(item.get("identifiers") or {})
    doi = item.get("doi") or identifiers.get("doi")
    url = item.get("url") or identifiers.get("url")
    links = item.get("links") if isinstance(item.get("links"), list) else []
    detail_link = item.get("url") or (links[0] if links else None)

    normalized = {
        "candidate_id": "",
        "provider": plan_item["provider"],
        "route": plan_item["route"],
        "title": str(item.get("title") or "").strip(),
        "journal": item.get("journal"),
        "year": item.get("year"),
        "language": item.get("language"),
        "doi": doi,
        "url": url,
        "identifiers": identifiers,
        "detail_link": detail_link,
    }
    if doi and not normalized["identifiers"].get("doi"):
        normalized["identifiers"]["doi"] = doi
    if url and not normalized["identifiers"].get("url"):
        normalized["identifiers"]["url"] = url
    normalized["candidate_id"] = _build_candidate_id(normalized)
    return normalized


def rank_candidates(
    candidates: Sequence[dict[str, Any]],
    *,
    expected_title: str | None = None,
    preferred_provider: str | None = None,
) -> list[dict[str, Any]]:
    """Rank candidates by title match, provider preference, and DOI presence."""
    normalized_expected_title = _normalize_title(expected_title)
    normalized_provider = str(preferred_provider or "").strip().lower() or None

    def _score(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
        normalized_title = _normalize_title(candidate.get("title"))
        exact_title = int(bool(normalized_expected_title and normalized_title == normalized_expected_title))
        provider_match = int(
            bool(normalized_provider and str(candidate.get("provider") or "").strip().lower() == normalized_provider)
        )
        has_doi = int(bool(_clean_identifier(candidate.get("doi") or (candidate.get("identifiers") or {}).get("doi"))))
        year_str = str(candidate.get("year") or "")
        try:
            year = int(year_str)
        except (ValueError, TypeError):
            year = 0
        year_score = min(year, 2026) if year >= 2000 else 0
        return (exact_title, provider_match, has_doi, year_score)

    return sorted(candidates, key=_score, reverse=True)


async def search_multilingual(
    *,
    target: str,
    disease: str,
    language: str = "auto",
    candidate_limit: int = 15,
    provider_hints: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Search across multiple providers with language-based routing.

    Calls gateway.search_provider directly (not workflow).
    """
    query = f"{target} {disease} case report".strip()
    if not query:
        return []

    plan = build_provider_plan(language=language, provider_hints=provider_hints)
    plan = get_health_tracker().reorder_plan(plan)
    collected: list[dict[str, Any]] = []
    preferred_provider = plan[0]["provider"] if plan else None

    for plan_item in plan:
        result = await search_provider(
            provider=plan_item["provider"],
            query=query,
            limit=candidate_limit,
        )
        items = normalize_items(result.provider, result.items) if result.success else []
        for item in items:
            collected.append(_normalize_candidate(item.model_dump(), plan_item))

        collected = dedupe_candidates(collected)
        collected = rank_candidates(collected, expected_title=target, preferred_provider=preferred_provider)
        if len(collected) >= candidate_limit:
            return collected[:candidate_limit]

    return rank_candidates(collected, expected_title=target, preferred_provider=preferred_provider)[:candidate_limit]


async def search_parallel(
    *,
    query: str,
    plan: list[ProviderPlanItem],
    concurrency: int = 4,
    candidate_limit: int = 15,
) -> list[dict[str, Any]]:
    """Search multiple providers concurrently, merge and dedupe results."""
    sem = asyncio.Semaphore(concurrency)
    preferred_provider = plan[0]["provider"] if plan else None

    async def _search_one(item: ProviderPlanItem) -> list[dict[str, Any]]:
        async with sem:
            result = await search_provider(
                provider=item["provider"],
                query=query,
                limit=candidate_limit,
            )
            items = normalize_items(result.provider, result.items) if result.success else []
            return [_normalize_candidate(i.model_dump(), item) for i in items]

    tasks = [_search_one(item) for item in plan]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    collected: list[dict[str, Any]] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            from loguru import logger as _logger

            _logger.warning("Provider {} search failed: {}", plan[i]["provider"], result)
            continue
        collected.extend(result)

    collected = dedupe_candidates(collected)
    ranked = rank_candidates(collected, expected_title=query, preferred_provider=preferred_provider)
    if rerank_enabled():
        from .rerank import neural_rerank

        ranked = await neural_rerank(query or "", ranked)
    return ranked[:candidate_limit]
