"""Collect 2025-2026 SOTA baseline results on the 30-variant reference set.

Adds new method keys to ``results/collected_results.json`` without touching
the frozen M1-M4 runs. Each method retrieves a broad PubMed candidate pool
and applies one 2025-2026 retrieval technique on top (dense retrieval,
cross-encoder reranking, hybrid sparse+dense+rerank, LLM query expansion,
agentic search loop, multilingual dense retrieval).

Usage::

    python benchmarks/collect_sota_baselines.py [--methods dense rerank hybrid expand agent mdense]
                                                [--limit 20] [--concurrency 4]

Environment (secrets never logged)::

    LIT_SOTA_CHAT_BASE_URL / LIT_SOTA_CHAT_API_KEY / LIT_SOTA_CHAT_MODEL
        LLM for expansion / agentic methods (default: project LLM config).
    LIT_SOTA_EMBED_BASE_URL / LIT_SOTA_EMBED_API_KEY / LIT_SOTA_EMBED_MODEL
        Embedding + rerank endpoint (default: project translation config).
    LIT_TRANSLATION_BASE_URL / LIT_TRANSLATION_API_KEY / LIT_TRANSLATION_MODEL
        Used by the multilingual dense method via ``translate_query``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from run_benchmark import RESULTS_DIR, load_variants  # noqa: E402

COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
MANIFEST_FILE = RESULTS_DIR / "sota_manifest.json"

# Keys that must NEVER be touched by this collector: the frozen 2026-08-12
# M1-M4 runs (incl. the non-nested full_pipeline variance reference).
FROZEN_METHODS = {
    "pubmed_only",
    "multisource_no_translation",
    "multisource_with_translation",
    "full_pipeline",
}

METHODS = {
    "dense": "sota_dense",
    "rerank": "sota_rerank",
    "hybrid": "sota_hybrid",
    "expand": "sota_expand",
    "agent": "sota_agent",
    "mdense": "sota_mdense",
}

METHOD_DESCRIPTIONS = {
    "sota_dense": "PubMed pool (pub date) + bge-m3 dense cosine rerank",
    "sota_rerank": "PubMed pool (pub date) + bge-reranker-v2-m3 cross-encoder",
    "sota_hybrid": "PubMed relevance + bge-m3 dense -> RRF(k=60) -> bge-reranker-v2-m3",
    "sota_expand": "PubMed + gpt-5 query expansion (3 variants, RRF-fused channels)",
    "sota_agent": "PubMed + gpt-5 2-round search-reason-search (RRF-fused rounds)",
    "sota_mdense": "PubMed pool + Qwen 6-language queries, bge-m3 max-similarity",
}


def _prompt_hashes() -> dict[str, str | None]:
    """SHA-256 (16 hex) of the LLM prompts used by each method."""
    out: dict[str, str | None] = {}
    try:
        from baselines.sota_expand import _SYSTEM_PROMPT as EXPAND_PROMPT
        from baselines.sota_agent import _SYSTEM_PROMPT as AGENT_PROMPT
        from lit_acquisition.query_translator import _SYSTEM_PROMPT as TRANS_PROMPT

        out["sota_expand"] = hashlib.sha256(EXPAND_PROMPT.encode()).hexdigest()[:16]
        out["sota_agent"] = hashlib.sha256(AGENT_PROMPT.encode()).hexdigest()[:16]
        out["sota_mdense"] = hashlib.sha256(TRANS_PROMPT.encode()).hexdigest()[:16]
    except Exception:
        pass
    return out


async def collect(
    methods: list[str],
    limit: int,
    concurrency: int,
) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], dict[str, dict[str, float]]]:
    """Run the selected SOTA methods.

    Returns ({method: {vid: [items]}}, {method: {vid: wall_s}}).
    """
    variants = load_variants()
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    timings: dict[str, dict[str, float]] = {}
    for key in methods:
        method = METHODS[key]
        mod = __import__(f"baselines.{method}", fromlist=["run_baseline"])
        print(f"\n{'=' * 60}\nRunning method: {method}\n{'=' * 60}")
        start = time.monotonic()
        results = await mod.run_baseline(variants, limit=limit, concurrency=concurrency)
        timings[method] = (
            {vid: round(t, 2) for vid, t in mod.timings.items()}
            if hasattr(mod, "timings")
            else {}
        )
        out[method] = results
        total = sum(len(v) for v in results.values())
        elapsed = round(time.monotonic() - start, 1)
        print(f"  {method}: {total} papers in {elapsed}s")
    return out, timings


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect 2025-2026 SOTA baselines")
    parser.add_argument("--methods", nargs="+", default=list(METHODS),
                        choices=list(METHODS), help="Subset of methods to run")
    parser.add_argument("--limit", type=int, default=20, help="Candidates per variant")
    parser.add_argument("--concurrency", type=int, default=4, help="Parallel variants")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    collected = (
        json.loads(COLLECTED_FILE.read_text())
        if COLLECTED_FILE.exists()
        else {}
    )

    started_at = datetime.now(timezone.utc).isoformat()
    new_results, timings = asyncio.run(collect(args.methods, args.limit, args.concurrency))

    # Merge; SOTA method keys may be refreshed on re-run, frozen M1-M4 never.
    for method, per_variant in new_results.items():
        if method in FROZEN_METHODS:
            raise SystemExit(f"refusing to overwrite frozen method key: {method}")
        collected[method] = per_variant
    COLLECTED_FILE.write_text(json.dumps(collected, ensure_ascii=False, indent=2))
    print(f"\nMerged {len(new_results)} methods into {COLLECTED_FILE}")

    manifest = {
        "started_at_utc": started_at,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "budget": f"{args.limit} candidates per variant",
        "pool": "PubMed esearch retmax=100; pub-date order for dense/rerank/"
                "expand/agent/mdense; relevance order for the hybrid sparse channel",
        "methods": {m: METHOD_DESCRIPTIONS[m] for m in new_results},
        "prompt_sha256_16": _prompt_hashes(),
        "llm": {
            "expansion_agent_model": __import__(
                "baselines.sota_common", fromlist=["chat_config"]
            ).chat_config()[2],
            "embedding_model": __import__(
                "baselines.sota_common", fromlist=["embed_config"]
            ).embed_config()[2],
        },
        "per_method_totals": {
            m: sum(len(v) for v in r.values()) for m, r in new_results.items()
        },
        "per_variant_wall_s": timings,
        "note": (
            "New methods only; frozen 2026-08-12 M1-M4 runs untouched. "
            "External single-source baselines collected 2026-08-13 remain "
            "in external_baselines.json."
        ),
    }
    MANIFEST_FILE.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Manifest saved to {MANIFEST_FILE}")


if __name__ == "__main__":
    main()
