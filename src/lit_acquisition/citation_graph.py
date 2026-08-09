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
from typing import Any, Literal

from loguru import logger

from .semantic_scholar import SemanticScholarService, get_semantic_scholar_service

TraversalDirection = Literal["citations", "references", "both"]


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
    resolved = await svc._resolve_paper_id(seed)
    if not resolved:
        logger.warning("citation_graph: could not resolve seed '{}'", seed[:80])
        return []

    visited: set[str] = set()
    results: list[dict[str, Any]] = []
    queue: list[tuple[str, int]] = [(resolved, 0)]

    while queue and len(results) < max_papers:
        batch = queue
        queue = []

        tasks: list[tuple[str, int]] = []
        for paper_id, depth in batch:
            if paper_id in visited:
                continue
            visited.add(paper_id)
            if depth >= max_depth:
                continue
            tasks.append((paper_id, depth))

        if not tasks:
            break

        # Fetch citations and references in parallel for each paper in the batch
        fetch_tasks = []
        for paper_id, depth in tasks:
            if direction in ("citations", "both"):
                fetch_tasks.append(_fetch_related(svc, paper_id, "citations", max_papers))
            if direction in ("references", "both"):
                fetch_tasks.append(_fetch_related(svc, paper_id, "references", max_papers))

        responses = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        for resp in responses:
            if isinstance(resp, Exception):
                logger.debug("citation_graph fetch error: {}", resp)
                continue
            for paper in resp:
                pid = paper.get("paperId")
                if not pid or pid in visited:
                    continue
                visited.add(pid)
                results.append(paper)
                # Enqueue for next depth level
                current_depth = tasks[0][1] if tasks else 0
                queue.append((pid, current_depth + 1))
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
    direction: Literal["citations", "references"],
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
