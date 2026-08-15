"""SOTA baseline: hybrid sparse+dense retrieval with cross-encoder rerank.

The canonical 2025-2026 biomedical retrieval recipe (BioASQ / TREC-COVID
line): (1) a sparse channel --- PubMed relevance-ranked search; (2) a
dense channel --- bge-m3 cosine retrieval over the same pool; (3)
Reciprocal Rank Fusion of the two ranked lists; (4) a cross-encoder
(bge-reranker-v2-m3) re-scores the fused top candidates. The cross-encoder
stage delivers the largest single quality boost in this pipeline.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

import httpx

from .sota_common import cosine, dedupe_items, embed_texts, pubmed_search, rerank_docs, tag

METHOD = "sota_hybrid"
RRF_K = 60
FUSE_TOPK = 100  # scales with the candidate budget (>=50)


async def _run_variant(client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
    # Sparse channel: PubMed relevance-ranked search (the public BM25-ish rank).
    sparse = await pubmed_search(client, query, retmax=100, sort="relevance")
    if not sparse:
        return []
    # Dense channel: bge-m3 over the same pool (query + titles).
    texts = [query] + [(p.get("title") or "") for p in sparse]
    vecs = await embed_texts(client, texts)
    qv, doc_vecs = vecs[0], vecs[1:]
    dense_order = sorted(
        range(len(sparse)), key=lambda i: cosine(qv, doc_vecs[i]), reverse=True
    )

    # RRF fusion.
    scores: dict[int, float] = defaultdict(float)
    for rank, idx in enumerate(range(len(sparse)), start=1):
        scores[idx] += 1.0 / (RRF_K + rank)
    for rank, idx in enumerate(dense_order, start=1):
        scores[idx] += 1.0 / (RRF_K + rank)
    fused = [sparse[i] for i in sorted(scores, key=lambda i: scores[i], reverse=True)[:max(FUSE_TOPK, limit)]]

    # Cross-encoder rerank of the fused shortlist.
    scored = await rerank_docs(client, query, fused, top_n=limit)
    return dedupe_items([d for _, d in scored[:limit]])


async def run_baseline(
    variants: list[dict[str, Any]], limit: int = 20, concurrency: int = 4
) -> dict[str, list[dict[str, Any]]]:
    """Run hybrid sparse+dense+rerank on all variants."""
    results: dict[str, list[dict[str, Any]]] = {}

    async def one(variant: dict[str, Any]) -> None:
        vid = variant["variant_id"]
        try:
            found = await _run_variant(client, variant["query"], limit)
            results[vid] = tag(found, METHOD)
        except Exception as exc:  # noqa: BLE001 - record and continue
            print(f"  [sota_hybrid] {vid} FAILED: {exc}")
            results[vid] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        sem = asyncio.Semaphore(concurrency)

        async def guarded(variant: dict[str, Any]) -> None:
            async with sem:
                await one(variant)

        await asyncio.gather(*(guarded(v) for v in variants))
    for vid, found in results.items():
        print(f"  [sota_hybrid] {vid}: {len(found)} papers")
    return results
