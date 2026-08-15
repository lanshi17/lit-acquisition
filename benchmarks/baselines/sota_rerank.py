"""SOTA baseline: cross-encoder reranking (bge-reranker-v2-m3).

Retrieves a broad PubMed candidate pool (pub-date order) and re-scores it
with a cross-encoder that jointly encodes query and candidate title. The
cross-encoder stage is repeatedly reported as the largest single quality
boost across lexical, dense, and hybrid pipelines (e.g., the BioASQ /
TREC-COVID hybrid-retrieval line, 2025).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .sota_common import dedupe_items, pubmed_search, rerank_docs, tag

METHOD = "sota_rerank"


async def _run_variant(client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
    pool = await pubmed_search(client, query, retmax=100, sort="pub date")
    if not pool:
        return []
    scored = await rerank_docs(client, query, pool, top_n=limit)
    return dedupe_items([d for _, d in scored[:limit]])


async def run_baseline(
    variants: list[dict[str, Any]], limit: int = 20, concurrency: int = 4
) -> dict[str, list[dict[str, Any]]]:
    """Run cross-encoder reranking on all variants."""
    results: dict[str, list[dict[str, Any]]] = {}

    async def one(variant: dict[str, Any]) -> None:
        vid = variant["variant_id"]
        try:
            found = await _run_variant(client, variant["query"], limit)
            results[vid] = tag(found, METHOD)
        except Exception as exc:  # noqa: BLE001 - record and continue
            print(f"  [sota_rerank] {vid} FAILED: {exc}")
            results[vid] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        sem = asyncio.Semaphore(concurrency)

        async def guarded(variant: dict[str, Any]) -> None:
            async with sem:
                await one(variant)

        await asyncio.gather(*(guarded(v) for v in variants))
    for vid, found in results.items():
        print(f"  [sota_rerank] {vid}: {len(found)} papers")
    return results
