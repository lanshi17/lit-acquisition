"""Candidate deduplication, identity, and normalization helpers (pure)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any

from ..models import ProviderPlanItem

_TITLE_NORMALIZER = re.compile(r"[^\w一-鿿぀-ヿ가-힯]+", re.UNICODE)


def normalize_title(title: str | None) -> str | None:
    """Lowercase and strip non-word characters from a title for comparison."""
    if not title:
        return None
    normalized = _TITLE_NORMALIZER.sub("", str(title).casefold())
    return normalized or None


def clean_identifier(value: Any) -> str | None:
    """Casefold/strip an identifier, returning None when empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text.casefold() or None


def candidate_keys(candidate: dict[str, Any]) -> list[tuple[str, str]]:
    """Dedup identity keys (doi/url/title) for a candidate."""
    identifiers = candidate.get("identifiers") or {}
    keys: list[tuple[str, str]] = []
    doi = clean_identifier(candidate.get("doi") or identifiers.get("doi"))
    if doi:
        keys.append(("doi", doi))
    url = clean_identifier(
        candidate.get("url") or candidate.get("detail_link") or identifiers.get("url") or identifiers.get("detail_link")
    )
    if url:
        keys.append(("url", url))
    normalized_title = normalize_title(candidate.get("title"))
    if normalized_title:
        keys.append(("title", normalized_title))
    return keys


def dedupe_candidates(candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate candidates by DOI, URL, or title."""
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        keys = candidate_keys(candidate)
        if keys and any(key in seen for key in keys):
            continue
        deduped.append(candidate)
        for key in keys:
            seen.add(key)
    return deduped


def build_candidate_id(candidate: dict[str, Any]) -> str:
    """Stable content hash for a candidate."""
    identity = {
        "provider": candidate.get("provider"),
        "route": candidate.get("route"),
        "doi": candidate.get("doi"),
        "url": candidate.get("url"),
        "title": candidate.get("title"),
    }
    digest = hashlib.sha1(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"cand-{digest[:12]}"


def normalize_candidate(item: dict[str, Any], plan_item: ProviderPlanItem) -> dict[str, Any]:
    """Merge a normalized item with its plan provenance into a candidate dict."""
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
    normalized["candidate_id"] = build_candidate_id(normalized)
    return normalized
