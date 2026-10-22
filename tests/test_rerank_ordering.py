"""Regression test: the cross-encoder helper must return relevance order.

The provider returns its rerank response already ordered by relevance, with the
original document position in ``index``. An earlier ``rerank_docs`` restored
input order, and its callers truncated with ``[:limit]`` --- which made the
recorded S2/S3 baselines and the M5/M5g artifacts no-ops with respect to the
cross-encoder. This test pins the corrected behaviour without touching the
network.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent.parent / "benchmarks"
sys.path.insert(0, str(BENCH_DIR))

from baselines import sota_common


def _docs() -> list[dict[str, str]]:
    return [
        {"title": "irrelevant aspirin trial"},
        {"title": "perfect MECP2 Rett syndrome match"},
        {"title": "lattice gauge theory"},
        {"title": "partially relevant Rett review"},
    ]


def test_rerank_docs_returns_score_order(monkeypatch) -> None:
    """Response order is by relevance; the helper must keep it."""

    async def fake_post_json(client, url, payload, api_key, *, timeout=60.0):
        # Provider order: best first, with the original index attached.
        return {
            "results": [
                {"index": 1, "relevance_score": 0.98},
                {"index": 3, "relevance_score": 0.41},
                {"index": 0, "relevance_score": 0.02},
                {"index": 2, "relevance_score": 0.0001},
            ]
        }

    monkeypatch.setattr(sota_common, "_post_json", fake_post_json)
    monkeypatch.setattr(sota_common, "rerank_config", lambda: ("http://x", "k", "m"))

    docs = _docs()
    scored = asyncio.run(sota_common.rerank_docs(None, "MECP2 Rett syndrome", docs))

    titles = [doc["title"] for _, doc in scored]
    scores = [score for score, _ in scored]
    assert titles == [
        "perfect MECP2 Rett syndrome match",
        "partially relevant Rett review",
        "irrelevant aspirin trial",
        "lattice gauge theory",
    ]
    assert scores == sorted(scores, reverse=True)


def test_rerank_docs_top_n_is_passed_through(monkeypatch) -> None:
    """``top_n`` reaches the payload (the provider then trims its own list)."""
    captured: dict[str, object] = {}

    async def fake_post_json(client, url, payload, api_key, *, timeout=60.0):
        captured.update(payload)
        return {"results": [{"index": 0, "relevance_score": 0.5}]}

    monkeypatch.setattr(sota_common, "_post_json", fake_post_json)
    monkeypatch.setattr(sota_common, "rerank_config", lambda: ("http://x", "k", "m"))

    asyncio.run(sota_common.rerank_docs(None, "q", _docs(), top_n=2))
    assert captured.get("top_n") == 2
