"""Baseline 3: Multi-source + LLM translation, NO citation graph.

Uses the full multilingual search (query translation into 6 languages,
parallel search across 18+ providers) but does NOT include citation
graph traversal. This isolates the contribution of citation graph augmentation.
"""

from __future__ import annotations

import asyncio
from typing import Any

from lit_acquisition import multilingual_acquisition_workflow
from lit_acquisition.normalizers import normalize_items


async def search_multisource_with_translation(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Run multilingual search without citation graph.

    Args:
        query: Original query string.
        limit: Maximum results.

    Returns:
        List of paper dicts with normalized fields.
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

    return results


async def run_baseline(variants: list[dict[str, Any]], limit: int = 20) -> dict[str, list[dict[str, Any]]]:
    """Run multi-source with-translation baseline on all variants."""
    results: dict[str, list[dict[str, Any]]] = {}
    for variant in variants:
        query = variant["query"]
        found = await search_multisource_with_translation(query, limit=limit)
        results[variant["variant_id"]] = found
        print(f"  [multisource_with_translation] {variant['variant_id']}: {len(found)} papers")
    return results
