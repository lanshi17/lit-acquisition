"""Citation graph traversal via Semantic Scholar API.

Starting from a seed paper (DOI, title, or Semantic Scholar paperId),
this module discovers related papers by traversing the citation graph.
It uses Semantic Scholar's ``/citations`` and ``/references`` endpoints
to find papers that cite or are cited by the seed.

This approach complements keyword search by finding topically related
papers that may not share exact query terms, improving recall.

Copyright note: Only metadata (titles, authors, DOIs, citation counts)
is collected. No full-text content is accessed during traversal.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from .enums import TraversalDirection
from .providers.semantic_scholar import SemanticScholarService, get_semantic_scholar_service


async def traverse_citation_graph(
    seed: str,
    *,
    max_depth: int = 1,
    max_papers: int = 50,
    direction: TraversalDirection = "both",
    service: SemanticScholarService | None = None,
) -> list[dict[str, Any]]:
    """Traverse the citation graph from a seed paper.

    Args:
        seed: Seed paper identifier (DOI, arXiv ID, or Semantic Scholar
            paperId). Plain DOIs starting with ``10.`` are auto-prefixed.
        max_depth: How many hops to traverse from the seed. Depth 1 means
            only direct citations/references. Depth 2 also traverses
            the citations/references of those results.
        max_papers: Maximum total papers to return (deduplicated).
        direction: Which edges to follow - ``"citations"`` (papers citing
            the seed), ``"references"`` (papers cited by the seed), or
            ``"both"``.
        service: Optional pre-built SemanticScholarService. If omitted,
            the process-wide singleton is used.

    Returns:
        List of related paper dicts (Semantic Scholar format) sorted by
        citation count descending. The seed paper itself is excluded.
    """
    svc = service or get_semantic_scholar_service()
    max_depth = max(1, min(max_depth, 3))
    max_papers = max(1, min(max_papers, 200))

    # Resolve the seed to a Semantic Scholar paperId
    resolved = await svc.resolve_paper_id(seed)
    if not resolved:
        logger.warning("citation_graph: could not resolve seed '{}'", seed[:80])
        return []

    # ``visited`` marks papers already expanded (or beyond max_depth);
    # ``seen`` deduplicates result papers. Keeping them separate matters:
    # marking papers as visited when they are enqueued made depth>=2
    # traversal skip every second-hop paper at dequeue time.
    visited: set[str] = set()
    seen: set[str] = {resolved}  # the seed itself is never returned
    results: list[dict[str, Any]] = []
    queue: list[tuple[str, int]] = [(resolved, 0)]

    # Semantic Scholar rate-limits aggressively; an unbounded gather over a
    # wide frontier can fire hundreds of concurrent requests and earn a
    # batch-wide 429. Bound the concurrency per fetch instead.
    semaphore = asyncio.Semaphore(8)

    async def _bounded_fetch(paper_id: str, relation: str, limit: int) -> list[dict[str, Any]]:
        async with semaphore:
            return await _fetch_related(svc, paper_id, relation, limit)

    while queue and len(results) < max_papers:
        batch = queue
        queue = []

        tasks: list[tuple[str, int]] = []
        for paper_id, depth in batch:
            if paper_id in visited:
                continue
            # Mark visited only when the paper is dequeued for expansion.
            visited.add(paper_id)
            if depth >= max_depth:
                continue
            tasks.append((paper_id, depth))

        if not tasks:
            break

        # Fetch citations and references in parallel for each paper in the batch
        fetch_tasks = []
        fetch_meta: list[tuple[str, int]] = []
        for paper_id, depth in tasks:
            if direction in ("citations", "both"):
                fetch_tasks.append(_bounded_fetch(paper_id, "citations", max_papers))
                fetch_meta.append((paper_id, depth))
            if direction in ("references", "both"):
                fetch_tasks.append(_bounded_fetch(paper_id, "references", max_papers))
                fetch_meta.append((paper_id, depth))

        responses = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        # gather() preserves order, so fetch_meta[i] describes responses[i].
        for (_, depth), resp in zip(fetch_meta, responses):
            if isinstance(resp, Exception):
                status_code = getattr(getattr(resp, "response", None), "status_code", None)
                logger.warning(
                    "citation_graph: fetch failed at depth {}{}: {}",
                    depth,
                    f" (HTTP {status_code})" if status_code is not None else "",
                    resp,
                )
                continue
            for paper in resp:
                pid = paper.get("paperId")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                results.append(paper)
                # Enqueue for the next depth level (depth tracked per task)
                queue.append((pid, depth + 1))
                if len(results) >= max_papers:
                    break
            if len(results) >= max_papers:
                break

    # Sort by citation count descending
    results.sort(key=lambda p: p.get("citationCount", 0), reverse=True)
    logger.info(
        "citation_graph: traversed from '{}', found {} related papers (depth={})",
        seed[:60],
        len(results),
        max_depth,
    )
    return results[:max_papers]


async def _fetch_related(
    service: SemanticScholarService,
    paper_id: str,
    direction: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch citations or references for a single paper.

    Args:
        service: SemanticScholarService instance.
        paper_id: Paper ID to fetch related papers for.
        direction: ``"citations"`` or ``"references"``.
        limit: Maximum papers to fetch.

    Returns:
        List of related paper dicts.
    """
    if direction == "citations":
        return await service.get_citations(paper_id, limit=limit)
    return await service.get_references(paper_id, limit=limit)
