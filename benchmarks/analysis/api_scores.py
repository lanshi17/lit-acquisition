"""Disk-cached embedding and cross-encoder scores for offline re-ranking analyses.

Motivation
----------
The 2026-08 SOTA collection consumed ``baselines.sota_common.rerank_docs``,
which restored the reranker response to *input* order (``sorted(..., key=r["index"])``)
instead of relevance order. Every caller then truncated the list with
``[:limit]``, so the recorded ``sota_rerank`` (S2) and ``sota_hybrid`` (S3)
baselines and the ``M5``/``M5g`` optimization artifacts carry the *unranked*
order. The helper is fixed in ``sota_common`` (regression test
``tests/test_rerank_ordering.py``); this module provides the corrected, cached
scoring path used by the offline experiments --- E1 deconfounding, E3 fair-pool
SOTA evaluation and E4c citation-seed sensitivity --- so that repeated analyses
never re-hit the API.

Both functions are pure lookups on top of an on-disk cache keyed by
``sha1(model | query | text)``; a cache miss is the only case that issues a
request. Cross-encoder scores live in a JSON file, embeddings in a compressed
``.npz`` (1024-dimensional float32 vectors). Cross-encoder scores depend only
on the (query, document) pair and bge-m3 embeddings only on the text, so
entries are reusable across candidate pools.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from baselines.sota_common import _post_json, embed_config, rerank_config  # noqa: E402

RESULTS_DIR = BENCH_DIR / "results"
DEFAULT_CACHE = RESULTS_DIR / "api_score_cache.json"
DEFAULT_EMBED_CACHE = RESULTS_DIR / "embedding_cache.npz"
BATCH = 50


def cache_path() -> Path:
    return Path(os.getenv("LIT_SCORE_CACHE", str(DEFAULT_CACHE)))


def load_cache() -> dict[str, Any]:
    path = cache_path()
    if path.exists():
        return json.loads(path.read_text())
    return {"schema_version": 1, "rerank": {}, "embed": {}}


def save_cache(cache: dict[str, Any]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False))


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


async def rerank_scores(
    client: httpx.AsyncClient,
    query: str,
    docs: Sequence[dict[str, Any]],
    cache: dict[str, Any],
    *,
    model: str | None = None,
    persist_every: int = 200,
) -> list[float]:
    """Cross-encoder relevance score per document (input order preserved)."""
    _base, key, default_model = rerank_config()
    model = model or default_model
    store = cache.setdefault("rerank", {})

    scores: list[float | None] = [None] * len(docs)
    pending: list[tuple[int, str, str]] = []  # (position, cache key, text)
    for i, doc in enumerate(docs):
        text = str(doc.get("title") or "").strip()
        k = _key("rerank", model, query, text)
        if k in store:
            scores[i] = float(store[k])
        else:
            pending.append((i, k, text))

    new_entries = 0
    for start in range(0, len(pending), BATCH):
        chunk = pending[start : start + BATCH]
        payload = {"model": model, "query": query, "documents": [text for _, _, text in chunk]}
        data = await _post_json(client, f"{_base}/rerank", payload, key)
        by_index = {int(row["index"]): float(row["relevance_score"]) for row in data.get("results", [])}
        for local, (position, k, _text) in enumerate(chunk):
            value = by_index.get(local, 0.0)
            scores[position] = value
            store[k] = value
            new_entries += 1
        if new_entries >= persist_every:
            save_cache(cache)
            new_entries = 0
    if new_entries:
        save_cache(cache)
    return [float(s or 0.0) for s in scores]


def order_by_scores(docs: Sequence[dict[str, Any]], scores: Sequence[float]) -> list[dict[str, Any]]:
    """Sort documents by descending score (stable for ties)."""
    return [doc for _, doc in sorted(zip(scores, docs), key=lambda pair: pair[0], reverse=True)]


def embed_cache_path() -> Path:
    return Path(os.getenv("LIT_EMBED_CACHE", str(DEFAULT_EMBED_CACHE)))


def _load_embed_store() -> "dict[str, Any]":
    """bge-m3 vectors live in an ``.npz`` (1024 floats each, float32)."""
    import numpy as np

    path = embed_cache_path()
    if path.exists():
        with np.load(path) as data:
            return {k: data[k] for k in data.files}
    return {}


def _save_embed_store(store: "dict[str, Any]") -> None:
    import numpy as np

    path = embed_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **store)


async def embed_texts(
    client: httpx.AsyncClient,
    texts: Sequence[str],
    cache: dict[str, Any] | None = None,
    *,
    model: str | None = None,
    persist_every: int = 200,
) -> list[list[float]]:
    """bge-m3 embedding per text (input order preserved), disk-cached as float32."""
    import numpy as np

    base, key, default_model = embed_config()
    model = model or default_model
    store = _load_embed_store()

    vectors: list[list[float] | None] = [None] * len(texts)
    pending: list[tuple[int, str, str]] = []
    for i, text in enumerate(texts):
        k = _key("embed", model, text)
        if k in store:
            vectors[i] = [float(x) for x in store[k]]
        else:
            pending.append((i, k, text))

    new_entries = 0
    for start in range(0, len(pending), BATCH):
        chunk = pending[start : start + BATCH]
        payload = {"model": model, "input": [text for _, _, text in chunk]}
        data = await _post_json(client, f"{base}/embeddings", payload, key)
        rows = sorted(data["data"], key=lambda r: int(r["index"]))
        for local, (position, k, _text) in enumerate(chunk):
            vector = rows[local]["embedding"] if local < len(rows) else []
            vectors[position] = vector
            store[k] = np.asarray(vector, dtype="float32")
            new_entries += 1
        if new_entries >= persist_every:
            _save_embed_store(store)
            new_entries = 0
    if new_entries:
        _save_embed_store(store)
    return [v or [] for v in vectors]


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    """Cosine similarity between two vectors."""
    va, vb = list(a), list(b)
    dot = sum(x * y for x, y in zip(va, vb))
    na = sum(x * x for x in va) ** 0.5
    nb = sum(x * x for x in vb) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


async def embed_and_rank(
    client: httpx.AsyncClient,
    query: str,
    docs: Sequence[dict[str, Any]],
    cache: dict[str, Any],
    *,
    model: str | None = None,
) -> tuple[list[dict[str, Any]], list[float]]:
    """Dense retrieval: rank documents by cosine similarity to the query."""
    titles = [str(doc.get("title") or "") for doc in docs]
    vectors = await embed_texts(client, [query, *titles], cache, model=model)
    query_vec, doc_vecs = vectors[0], vectors[1:]
    sims = [cosine(query_vec, vec) for vec in doc_vecs]
    return order_by_scores(docs, sims), sims


def warm_cache_size(cache: dict[str, Any]) -> dict[str, int]:
    return {"rerank": len(cache.get("rerank", {})), "embed": len(_load_embed_store())}


__all__ = [
    "cache_path",
    "cosine",
    "embed_and_rank",
    "embed_texts",
    "embed_cache_path",
    "load_cache",
    "order_by_scores",
    "rerank_scores",
    "save_cache",
    "warm_cache_size",
    "_key",
]
