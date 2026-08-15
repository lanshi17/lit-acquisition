"""SOTA baseline: dense retrieval with bge-m3 over a broad PubMed pool.

Retrieves a broad candidate pool via PubMed esearch (pub-date order, the
same channel behavior as the M1 baseline) and re-ranks it by cosine
similarity between the bge-m3 embedding of the query and the bge-m3
embedding of each candidate title. Represents the 2025-2026 dense
retriever line (mE5/mGTE/BGE) applied to scholarly metadata search.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .sota_common import cosine, dedupe_items, embed_texts, pubmed_search, tag

METHOD = "sota_dense"


async def _run_variant(client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
    pool = await pubmed_search(client, query, retmax=100, sort="pub date")
    if not pool:
        return []
    texts = [query] + [(p.get("title") or "") for p in pool]
    vecs = await embed_texts(client, texts)
    qv, doc_vecs = vecs[0], vecs[1:]
    scored = sorted(
        zip(doc_vecs, pool), key=lambda t: cosine(qv, t[0]), reverse=True
    )
    return dedupe_items([d for _, d in scored[:limit]])


async def run_baseline(
    variants: list[dict[str, Any]], limit: int = 20, concurrency: int = 4
) -> dict[str, list[dict[str, Any]]]:
    """Run dense retrieval on all variants."""
    results: dict[str, list[dict[str, Any]]] = {}

    async def one(variant: dict[str, Any]) -> None:
        vid = variant["variant_id"]
        try:
            found = await _run_variant(client, variant["query"], limit)
            results[vid] = tag(found, METHOD)
        except Exception as exc:  # noqa: BLE001 - record and continue
            print(f"  [sota_dense] {vid} FAILED: {exc}")
            results[vid] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        sem = asyncio.Semaphore(concurrency)

        async def guarded(variant: dict[str, Any]) -> None:
            async with sem:
                await one(variant)

        await asyncio.gather(*(guarded(v) for v in variants))
    for vid, found in results.items():
        print(f"  [sota_dense] {vid}: {len(found)} papers")
    return results
