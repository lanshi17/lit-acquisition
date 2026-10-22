"""Baseline 3: Multi-source + LLM translation, NO citation graph.

Uses the full multilingual search (query translation into 6 languages,
parallel search across 18+ providers) but does NOT include citation
graph traversal. This isolates the contribution of citation graph augmentation.
"""

from __future__ import annotations

import asyncio
from typing import Any

from lit_acquisition import multilingual_acquisition_workflow
from lit_acquisition.normalize import normalize_items


async def search_multisource_with_translation(
    query: str,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Run multilingual search without citation graph.

    Args:
        query: Original query string.
        limit: Maximum results.

    Returns:
        (paper dicts with normalized fields,
         per-language translated queries used for the search)
    """
    result = await multilingual_acquisition_workflow({
        "query": query,
        "action": "search",
        "limit": limit,
        "language": "auto",
        "relevance_gate": False,  # Disable LLM gate for fair comparison
    })

    results: list[dict[str, Any]] = []
    for item_dict in result.get("items", []):
        item_dict["method"] = "multisource_with_translation"
        results.append(item_dict)

    translations = (result.get("raw") or {}).get("translations") or {}
    return results, translations


async def run_baseline(
    variants: list[dict[str, Any]],
    limit: int = 20,
    translations_out: dict[str, dict[str, str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Run multi-source with-translation baseline on all variants.

    Args:
        translations_out: optional dict populated with
            variant_id -> {language: translated query} for auditability.
    """
    results: dict[str, list[dict[str, Any]]] = {}
    for variant in variants:
        query = variant["query"]
        found, translations = await search_multisource_with_translation(query, limit=limit)
        results[variant["variant_id"]] = found
        if translations_out is not None:
            translations_out[variant["variant_id"]] = translations
        print(f"  [multisource_with_translation] {variant['variant_id']}: {len(found)} papers")
    return results
