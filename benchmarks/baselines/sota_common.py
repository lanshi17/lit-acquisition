"""Shared infrastructure for 2025-2026 SOTA baseline methods.

All baselines retrieve a broad candidate pool from PubMed (the standard
single channel) and then apply a 2025-2026 state-of-the-art technique on
top: dense retrieval (BAAI/bge-m3), cross-encoder reranking
(BAAI/bge-reranker-v2-m3), hybrid sparse+dense fusion with reranking,
LLM query expansion, an agentic search-reason-search loop, and
multilingual dense retrieval with LLM-translated queries.

Configuration is via environment variables (consistent with the rest of
the project):

- ``LIT_SOTA_EMBED_BASE_URL`` / ``LIT_SOTA_EMBED_API_KEY`` (defaults to the
  SiliconFlow-compatible endpoint) for embeddings + reranking.
- ``LIT_SOTA_CHAT_BASE_URL`` / ``LIT_SOTA_CHAT_API_KEY`` /
  ``LIT_SOTA_CHAT_MODEL`` (defaults to the project LLM config) for
  expansion / agentic LLM calls.
- ``LIT_SOTA_TRANSLATION_*`` (defaults to the project translation config)
  for multilingual query translation.

Keys are never logged.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lit_acquisition import get_pubmed_service  # noqa: E402
from metrics import paper_id  # noqa: E402

EMBED_MODEL = "BAAI/bge-m3"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_EMBED_BASE = "https://api.siliconflow.cn/v1"
DEFAULT_CHAT_BASE = "https://linxi.chat/v1"
CHAT_MODEL = "gpt-5"
POOL_SIZE = 100  # broad PubMed candidate pool per variant
RETRY_ATTEMPTS = 4
BATCH = 32  # embedding batch size


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def embed_config() -> tuple[str, str, str]:
    base = _env("LIT_SOTA_EMBED_BASE_URL") or _env("LIT_TRANSLATION_BASE_URL") or DEFAULT_EMBED_BASE
    key = _env("LIT_SOTA_EMBED_API_KEY") or _env("LIT_TRANSLATION_API_KEY")
    model = _env("LIT_SOTA_EMBED_MODEL") or EMBED_MODEL
    if not key:
        raise RuntimeError("embedding API key missing: set LIT_SOTA_EMBED_API_KEY")
    return base, key, model


def chat_config() -> tuple[str, str, str]:
    base = _env("LIT_SOTA_CHAT_BASE_URL") or _env("LIT_LLM_BASE_URL") or DEFAULT_CHAT_BASE
    key = _env("LIT_SOTA_CHAT_API_KEY") or _env("LIT_LLM_API_KEY")
    model = _env("LIT_SOTA_CHAT_MODEL") or _env("LIT_LLM_MODEL") or CHAT_MODEL
    if not key:
        raise RuntimeError("chat API key missing: set LIT_SOTA_CHAT_API_KEY")
    return base, key, model


# ── HTTP helpers ─────────────────────────────────────────────────────────


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    api_key: str,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                timeout=timeout,
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last = exc
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"request failed after {RETRY_ATTEMPTS} attempts: {last}")


async def embed_texts(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    """Embed a list of texts with bge-m3 (batched, order preserved)."""
    base, key, model = embed_config()
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i : i + BATCH]
        payload = {"model": model, "input": chunk}
        data = await _post_json(client, f"{base}/embeddings", payload, key)
        rows = sorted(data["data"], key=lambda r: r["index"])
        out.extend(r["embedding"] for r in rows)
    return out


def rerank_config() -> tuple[str, str, str]:
    base, key, _ = embed_config()
    model = _env("LIT_SOTA_RERANK_MODEL") or RERANK_MODEL
    return base, key, model


async def rerank_docs(
    client: httpx.AsyncClient,
    query: str,
    docs: list[dict[str, Any]],
    top_n: int | None = None,
) -> list[tuple[float, dict[str, Any]]]:
    """Cross-encoder rerank (bge-reranker-v2-m3) over document dicts.

    Scores titles; returns (score, doc) pairs sorted by score descending.
    """
    base, key, model = rerank_config()
    texts = [d.get("title") or "" for d in docs]
    payload: dict[str, Any] = {"model": model, "query": query, "documents": texts}
    if top_n is not None:
        payload["top_n"] = top_n
    data = await _post_json(client, f"{base}/rerank", payload, key)
    results = sorted(data["results"], key=lambda r: r["index"])
    return [(r["relevance_score"], docs[r["index"]]) for r in results]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# ── LLM chat (expansion / agentic methods) ──────────────────────────────


async def chat_once(
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 2000,
    temperature: float = 0.2,
) -> str:
    base, key, model = chat_config()
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    data = await _post_json(client, f"{base}/chat/completions", payload, key)
    return data["choices"][0]["message"]["content"] or ""


def extract_json(text: str) -> Any:
    """Extract the first JSON object/array from an LLM response."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\[.*\]|\{.*\})", text, re.S)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


# ── PubMed pool retrieval ───────────────────────────────────────────────


# E-utilities without an API key allows ~3 requests/sec. We enforce a
# process-wide minimum interval so concurrent baselines never trip 429s.
_pubmed_gate = asyncio.Lock()
_last_pubmed_at = 0.0
PUBMED_MIN_INTERVAL = 0.45


async def _throttled_get(
    client: httpx.AsyncClient, url: str, params: dict[str, Any]
) -> httpx.Response:
    """GET with process-wide PubMed politeness + 429/5xx retry."""
    global _last_pubmed_at
    for attempt in range(6):
        async with _pubmed_gate:
            now = asyncio.get_event_loop().time()
            wait = PUBMED_MIN_INTERVAL - (now - _last_pubmed_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                resp = await client.get(url, params=params, timeout=30.0)
            finally:
                _last_pubmed_at = asyncio.get_event_loop().time()
        if resp.status_code in (429, 500, 502, 503, 504):
            await asyncio.sleep(1.5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("PubMed request failed after retries (persistent 429)")


async def pubmed_search(
    client: httpx.AsyncClient,
    query: str,
    retmax: int = POOL_SIZE,
    sort: str = "pub date",
) -> list[dict[str, Any]]:
    """esearch + esummary with the same item schema as the M1 baseline.

    Returns item dicts with doi/pmid/pmcid/title/journal/year/url and
    ``source: "pubmed"``. Order = PubMed's order for the given sort.
    """
    svc = get_pubmed_service()
    params: dict[str, Any] = {
        "db": "pubmed",
        "term": query,
        "retmax": max(1, min(retmax, 100)),
        "retmode": "json",
        "sort": sort,
    }
    if svc.api_key:
        params["api_key"] = svc.api_key
    base = svc.base_url

    resp = await _throttled_get(client, f"{base}/esearch.fcgi", params)
    pmids = resp.json().get("esearchresult", {}).get("idlist", []) or []
    if not pmids:
        return []

    summary_params: dict[str, Any] = {"db": "pubmed", "id": ",".join(pmids), "retmode": "json"}
    if svc.api_key:
        summary_params["api_key"] = svc.api_key
    resp = await _throttled_get(client, f"{base}/esummary.fcgi", params=summary_params)
    records = resp.json().get("result", {})

    items: list[dict[str, Any]] = []
    for pmid in pmids:
        row = records.get(pmid, {})
        if not row:
            continue
        doi = ""
        for aid in row.get("articleids", []) or []:
            if aid.get("idtype") == "doi":
                doi = aid.get("value", "")
                break
        pmcid = next(
            (a.get("value", "") for a in row.get("articleids", []) or []
             if a.get("idtype") == "pmc"),
            "",
        )
        items.append(
            {
                "doi": doi or None,
                "pmid": pmid,
                "pmcid": pmcid or None,
                "title": row.get("title", ""),
                "journal": row.get("fulljournalname") or row.get("source", ""),
                "year": (row.get("pubdate") or "")[:4] or None,
                "source": "pubmed",
                "language": None,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            }
        )
    return items


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicates by canonical paper identity, preserving order."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in items:
        pid = paper_id(p)
        if pid and pid in seen:
            continue
        if pid:
            seen.add(pid)
        out.append(p)
    return out


def tag(items: list[dict[str, Any]], method: str) -> list[dict[str, Any]]:
    for p in items:
        p["method"] = method
    return items
