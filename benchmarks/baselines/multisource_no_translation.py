"""Baseline 2: Multi-source search WITHOUT LLM query translation.

Searches 18+ providers in parallel using the original English query.
This isolates the contribution of LLM-based multilingual query translation.
"""

from __future__ import annotations

import asyncio
from typing import Any

from lit_acquisition import build_provider_plan, search_parallel
from lit_acquisition.normalizers import normalize_items


async def search_multisource_no_translation(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search all providers with original query (no translation).

    Args:
        query: Original English query string.
        limit: Maximum results.

    Returns:
        List of paper dicts with normalized fields.
    """
    plan = build_provider_plan(language="en")
    candidates = await search_parallel(
        query=query,
        plan=plan,
        concurrency=4,
        candidate_limit=limit,
    )

    results: list[dict[str, Any]] = []
    for c in candidates:
        provider = c.get("provider", "unknown")
        try:
            normalized = normalize_items(provider, [c])
            for item in normalized:
                d = item.model_dump()
                d["source"] = provider
                d["method"] = "multisource_no_translation"
                results.append(d)
        except Exception:
            pass

    return results


async def run_baseline(variants: list[dict[str, Any]], limit: int = 20) -> dict[str, list[dict[str, Any]]]:
    """Run multi-source no-translation baseline on all variants."""
    results: dict[str, list[dict[str, Any]]] = {}
    for variant in variants:
        query = variant["query"]
        found = await search_multisource_no_translation(query, limit=limit)
        results[variant["variant_id"]] = found
        print(f"  [multisource_no_translation] {variant['variant_id']}: {len(found)} papers")
    return results
