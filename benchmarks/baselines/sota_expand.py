"""SOTA baseline: LLM query expansion over PubMed (RRF-fused channels).

An LLM (gpt-5) generates three alternative English search queries that
re-express the clinical genetics query with synonyms, MeSH-style terms,
and evidence-type phrasing, preserving gene symbols and variant
nomenclature verbatim. Each channel (original + expansions) is retrieved
independently from PubMed; Reciprocal Rank Fusion merges the channels so
that genuinely new papers from the expanded channels can enter the
final top-K. This mirrors the widely used LLM-driven query-expansion line
in 2025-2026 retrieval pipelines.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

import httpx

from .sota_common import chat_once, dedupe_items, extract_json, paper_id, pubmed_search, tag

METHOD = "sota_expand"
N_VARIANTS = 3
RRF_K = 60
CHANNEL_SIZE = 100  # scales with the candidate budget

_SYSTEM_PROMPT = (
    "You are a biomedical search strategist for PubMed. Given a clinical "
    "genetics query (gene, variant, disease), produce exactly "
    f"{N_VARIANTS} alternative English PubMed search queries that could "
    "surface additional relevant papers the original query would miss. "
    "Keep gene symbols and variant nomenclature (e.g. p.G12D, c.5266dupC) "
    "verbatim; vary the disease phrasing, add evidence-type terms "
    "(e.g. 'case report', 'functional study', 'genotype-phenotype'), or "
    "MeSH-style synonyms. Each query must be a plain keyword string, "
    "10-25 words, no field tags, no boolean operators. "
    'Respond with a JSON array of strings only, e.g. '
    '["query one", "query two", "query three"].'
)


async def _run_variant(client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Original query: {query}"},
    ]
    raw = await chat_once(client, messages, max_tokens=4000, temperature=0.2)
    parsed = extract_json(raw)
    if not isinstance(parsed, list):
        raise RuntimeError(f"expansion response not a JSON list: {raw[:200]!r}")
    variants = [str(q).strip() for q in parsed if isinstance(q, str) and q.strip()][:N_VARIANTS]

    # Retrieve each channel independently.
    channels: list[list[dict[str, Any]]] = []
    for q in [query, *variants]:
        found = await pubmed_search(client, q, retmax=max(CHANNEL_SIZE, limit), sort="pub date")
        channels.append(dedupe_items(found))

    # RRF fusion across channels (k=60); ties broken by channel order.
    scores: dict[str, float] = defaultdict(float)
    first_seen: dict[str, dict[str, Any]] = {}
    for ch in channels:
        for rank, p in enumerate(ch, start=1):
            pid = paper_id(p)
            if not pid:
                continue
            scores[pid] += 1.0 / (RRF_K + rank)
            first_seen.setdefault(pid, p)
    fused = [first_seen[pid] for pid in sorted(scores, key=scores.get, reverse=True)]
    return fused[:limit]


async def run_baseline(
    variants: list[dict[str, Any]], limit: int = 20, concurrency: int = 3
) -> dict[str, list[dict[str, Any]]]:
    """Run LLM query expansion (RRF fusion) on all variants."""
    results: dict[str, list[dict[str, Any]]] = {}

    async def one(variant: dict[str, Any]) -> None:
        vid = variant["variant_id"]
        try:
            found = await _run_variant(client, variant["query"], limit)
            results[vid] = tag(found, METHOD)
        except Exception as exc:  # noqa: BLE001 - record and continue
            print(f"  [sota_expand] {vid} FAILED: {exc}")
            results[vid] = []

    async with httpx.AsyncClient(timeout=90.0) as client:
        sem = asyncio.Semaphore(concurrency)

        async def guarded(variant: dict[str, Any]) -> None:
            async with sem:
                await one(variant)

        await asyncio.gather(*(guarded(v) for v in variants))
    for vid, found in results.items():
        print(f"  [sota_expand] {vid}: {len(found)} papers")
    return results
