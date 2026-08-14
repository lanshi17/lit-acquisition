"""Baseline 1: PubMed-only search.

Uses PubMed esearch + esummary to find papers. This is the most common
approach used by clinical geneticists - a simple PubMed keyword search.
"""

from __future__ import annotations

import asyncio
from typing import Any

from lit_acquisition import get_pubmed_service
from lit_acquisition.normalizers import normalize_items


async def search_pubmed_only(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search PubMed only, return normalized items as dicts.

    Args:
        query: Search query string.
        limit: Maximum results.

    Returns:
        List of paper dicts with normalized fields.
    """
    svc = get_pubmed_service()
    candidates = await svc.search_candidates(query, candidate_limit=limit)

    results: list[dict[str, Any]] = []
    for c in candidates:
        item = {
            "doi": c.doi,
            "pmid": c.pmid,
            "pmcid": c.pmcid,
            "title": c.title,
            "journal": c.journal,
            "year": c.pub_date[:4] if c.pub_date else None,
            "source": "pubmed",
            # esummary does not expose article language; leave unknown
            # rather than fabricating "en" (PubMed also indexes
            # non-English articles under English titles).
            "language": None,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{c.pmid}/" if c.pmid else None,
        }
        results.append(item)

    return results


async def run_baseline(variants: list[dict[str, Any]], limit: int = 20) -> dict[str, list[dict[str, Any]]]:
    """Run PubMed-only baseline on all variants.

    Returns:
        Dict mapping variant_id -> list of found papers.
    """
    results: dict[str, list[dict[str, Any]]] = {}
    for variant in variants:
        query = variant["query"]
        found = await search_pubmed_only(query, limit=limit)
        for paper in found:
            paper["method"] = "pubmed_only"
        results[variant["variant_id"]] = found
        print(f"  [pubmed_only] {variant['variant_id']}: {len(found)} papers")
    return results
