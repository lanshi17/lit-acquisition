"""Optional neural reranking stage (cross-encoder, bge-reranker-v2-m3).

Federation merges heterogeneous providers into large candidate pools; the
heuristic ranker (title match, provider preference, DOI, recency) is not
relevance-sorted, and at deeper budgets it buries relevant papers below
shallow cutoffs (measured: the pipeline's Recall@20 fell below PubMed's at
50 candidates/variant). This module provides an opt-in final ranking stage
that re-scores candidates with a cross-encoder against the query --- the
single largest quality lever in 2025-2026 hybrid retrieval pipelines.

Enabled per-request or via the ``LIT_RERANK_ENABLED`` environment
variable; when no rerank endpoint is configured, the stage is a no-op and
the heuristic order is preserved (backward compatible).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from loguru import logger

from ..config import get_config
from ..net.pool import get_shared_client, resolve_provider_proxy

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
BATCH = 50
_RETRIES = 3
_RETRY_AFTER_CAP = 60.0


def rerank_enabled() -> bool:
    """Whether the rerank stage is enabled (env opt-in)."""
    return os.getenv("LIT_RERANK_ENABLED", "").strip().lower() in ("1", "true", "yes")


def _endpoint() -> tuple[str, str, str] | None:
    """Return (base_url, api_key, model) for the rerank endpoint, or None."""
    cfg = get_config()
    base = cfg.translation.base_url or cfg.llm.base_url
    key = (cfg.translation.all_api_keys or [""])[0] or cfg.llm.api_key
    if not base or not key:
        return None
    model = os.getenv("LIT_RERANK_MODEL", "").strip() or DEFAULT_MODEL
    return base.rstrip("/"), key, model


async def neural_rerank(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Re-rank candidates by cross-encoder relevance to *query*.

    Returns the candidates sorted by descending relevance score. Falls
    back to the input order when no endpoint is configured or on failure
    (the rerank stage must never break acquisition).
    """
    if not candidates:
        return candidates
    resolved = _endpoint()
    if resolved is None:
        return candidates
    url, key, resolved_model = resolved
    if model:
        resolved_model = model
    if base_url:
        url = base_url.rstrip("/")
    if api_key:
        key = api_key

    docs = [str(c.get("title") or "").strip() for c in candidates]
    try:
        # Shared pooled client (proxy-aware, keep-alive). The pool owns the
        # client - do NOT close it here. Per-request timeout is set in
        # _post_rerank.
        client = get_shared_client(proxy=resolve_provider_proxy(url))
        scores: list[float] = []
        for i in range(0, len(docs), BATCH):
            chunk = docs[i : i + BATCH]
            payload = {"model": resolved_model, "query": query, "documents": chunk}
            data = await _post_rerank(client, url, key, payload)
            by_index = {r["index"]: r["relevance_score"] for r in data.get("results", [])}
            scores.extend(by_index.get(j, 0.0) for j in range(len(chunk)))
        ordered = sorted(zip(scores, candidates), key=lambda t: t[0], reverse=True)
        return [c for _, c in ordered]
    except Exception as exc:
        logger.warning("neural rerank failed, keeping heuristic order: {}", exc)
        return candidates


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Parse ``Retry-After`` (delay-seconds or HTTP-date) into seconds.

    Returns None when the header is absent or unparseable.
    """
    value = resp.headers.get("Retry-After", "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, (when - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


async def _post_rerank(
    client: httpx.AsyncClient,
    url: str,
    key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            resp = await client.post(
                f"{url}/rerank",
                json=payload,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                timeout=60.0,
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {resp.status_code}")
                delay = 1.0 * (attempt + 1)
                if resp.status_code == 429:
                    retry_after = _retry_after_seconds(resp)
                    if retry_after is not None:
                        delay = min(retry_after, _RETRY_AFTER_CAP)
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.TransportError as exc:
            last = exc
            await asyncio.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"rerank request failed after {_RETRIES} attempts: {last}")
