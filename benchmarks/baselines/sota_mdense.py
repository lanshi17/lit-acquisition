"""SOTA baseline: multilingual dense retrieval (LLM translation + bge-m3).

Tests the 2025-2026 CLIR hypothesis that a multilingual dense retriever
replaces the translation-then-lexical pipeline: the query is translated
into six languages by an LLM (same prompt as the M3 pipeline), and
retrieval is bge-m3 cosine similarity over the PubMed candidate pool,
where a candidate's score is the maximum similarity over all language
queries. No lexical matching against translated text is performed. This
isolates whether multilingual dense ranking recovers the German/French
papers that English-only keyword search misses (CLIRudit-style claim).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from openai import AsyncOpenAI

from .sota_common import (
    cosine,
    dedupe_items,
    embed_config,
    embed_texts,
    pubmed_search,
    tag,
)

METHOD = "sota_mdense"


async def _translate_queries(query: str) -> list[str]:
    """Translate into the same 6 languages as the M3 pipeline (Qwen LLM)."""
    from lit_acquisition import translate_query

    base, key, _model = embed_config()
    # Translation uses the project translation config; embed config is the
    # SiliconFlow-compatible endpoint that already hosts the Qwen model.
    llm = AsyncOpenAI(base_url=base, api_key=key, timeout=60.0)
    try:
        tr = await translate_query(query, client=llm)
    finally:
        await llm.close()
    d = tr.as_dict()
    return [str(v) for v in d.values() if str(v).strip()]


async def _run_variant(client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
    langs = await _translate_queries(query)
    pool = await pubmed_search(client, query, retmax=100, sort="pub date")
    if not pool:
        return []

    # Embed all language queries + all candidate titles; score = max over langs.
    qvecs = await embed_texts(client, langs)
    doc_vecs = await embed_texts(client, [p.get("title") or "" for p in pool])
    scores = [
        max(cosine(qv, dv) for qv in qvecs) if qvecs else 0.0
        for dv in doc_vecs
    ]
    order = sorted(range(len(pool)), key=lambda i: scores[i], reverse=True)
    return dedupe_items([pool[i] for i in order[:limit]])


async def run_baseline(
    variants: list[dict[str, Any]], limit: int = 20, concurrency: int = 3
) -> dict[str, list[dict[str, Any]]]:
    """Run multilingual dense retrieval on all variants."""
    results: dict[str, list[dict[str, Any]]] = {}

    async def one(variant: dict[str, Any]) -> None:
        vid = variant["variant_id"]
        try:
            found = await _run_variant(client, variant["query"], limit)
            results[vid] = tag(found, METHOD)
        except Exception as exc:  # noqa: BLE001 - record and continue
            print(f"  [sota_mdense] {vid} FAILED: {exc}")
            results[vid] = []

    async with httpx.AsyncClient(timeout=90.0) as client:
        sem = asyncio.Semaphore(concurrency)

        async def guarded(variant: dict[str, Any]) -> None:
            async with sem:
                await one(variant)

        await asyncio.gather(*(guarded(v) for v in variants))
    for vid, found in results.items():
        print(f"  [sota_mdense] {vid}: {len(found)} papers")
    return results
