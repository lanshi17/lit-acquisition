"""SOTA baseline: agentic search-reason-search loop (2 rounds, RRF fusion).

Represents the 2025-2026 "deep research agent" pattern applied to a
single scholarly channel: round 1 retrieves from PubMed; the agent
reviews the top titles and reasons about gaps; round 2 issues one
refined/complementary query; both rounds are fused with Reciprocal Rank
Fusion so round-2 findings can enter the final top-K. The agent has no
access to the gold set --- only to the titles PubMed returned.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

import httpx

from .sota_common import chat_once, dedupe_items, extract_json, paper_id, pubmed_search, tag

METHOD = "sota_agent"
ROUND1 = 100  # scales with the candidate budget
ROUND2 = 100
RRF_K = 60

_SYSTEM_PROMPT = (
    "You are a clinical genetics literature search agent using PubMed. "
    "You will be given the original query and the titles of the top "
    "results. Identify what relevant evidence the current result list "
    "appears to be missing (e.g. case reports, functional studies, "
    "genotype-phenotype cohorts, non-English studies indexed under "
    "English titles). Then produce exactly ONE refined PubMed search "
    "query that targets the gap, as a plain keyword string of 10-25 "
    "words, preserving gene symbols and variant nomenclature verbatim, "
    "no field tags or boolean operators. "
    'Respond with a JSON object: {"reasoning": "...", "query": "..."}.'
)


async def _run_variant(client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
    round1 = await pubmed_search(client, query, retmax=max(ROUND1, limit), sort="pub date")
    titles = "\n".join(f"- {p.get('title') or '(no title)'}" for p in round1[:15])
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Original query: {query}\n\nTop results so far:\n{titles}"
            ),
        },
    ]
    raw = await chat_once(client, messages, max_tokens=8000, temperature=0.2)
    parsed = extract_json(raw)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("query"), str):
        raise RuntimeError(f"agent response malformed: {raw[:200]!r}")
    refined = parsed["query"].strip()
    round2 = (
        await pubmed_search(client, refined, retmax=max(ROUND2, limit), sort="pub date")
        if refined
        else []
    )

    # RRF fusion of the two rounds; round-1 rank dominates ties.
    scores: dict[str, float] = defaultdict(float)
    first_seen: dict[str, dict[str, Any]] = {}
    for papers in (dedupe_items(round1), dedupe_items(round2)):
        for rank, p in enumerate(papers, start=1):
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
    """Run the 2-round agentic loop on all variants."""
    results: dict[str, list[dict[str, Any]]] = {}

    async def one(variant: dict[str, Any]) -> None:
        vid = variant["variant_id"]
        try:
            found = await _run_variant(client, variant["query"], limit)
            results[vid] = tag(found, METHOD)
        except Exception as exc:  # noqa: BLE001 - record and continue
            print(f"  [sota_agent] {vid} FAILED: {exc}")
            results[vid] = []

    async with httpx.AsyncClient(timeout=90.0) as client:
        sem = asyncio.Semaphore(concurrency)

        async def guarded(variant: dict[str, Any]) -> None:
            async with sem:
                await one(variant)

        await asyncio.gather(*(guarded(v) for v in variants))
    for vid, found in results.items():
        print(f"  [sota_agent] {vid}: {len(found)} papers")
    return results
