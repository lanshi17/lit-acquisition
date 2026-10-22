"""Main benchmark runner.

Usage:
    python run_benchmark.py --collect      # Run all methods, collect results
    python run_benchmark.py --compute      # Compute metrics + paper data
    python run_benchmark.py --analyze      # Print analysis tables
    python run_benchmark.py --all          # Run all steps

Collection protocol (corrected):

- Every method receives the SAME candidate budget (``--limit``, default 50)
  so that fixed-K metrics are comparable across methods.
- M4 (full pipeline) is constructed as a STRICTLY NESTED extension of M3:
  the citation-graph traversal is seeded from the frozen M3 candidate list
  and its results are appended to that frozen list. The keyword phase is
  never re-executed for M4, so component attribution is not confounded by
  live-API run-to-run variance, and Recall@K(M4) >= Recall@K(M3) holds by
  construction.
- Translated queries, per-variant wall-clock timings, and a run manifest
  (config echo, provider failure context, versions) are persisted alongside
  the raw candidate lists.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add parent dir to path for lit_acquisition import
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Add benchmarks dir to path for local imports
sys.path.insert(0, str(Path(__file__).parent))

from metrics import paper_id

from lit_acquisition.rerank import rerank_enabled  # noqa: E402

BENCHMARK_DIR = Path(__file__).parent
GOLD_STANDARD_FILE = BENCHMARK_DIR / "gold_standard" / "variants.jsonl"
RESULTS_DIR = BENCHMARK_DIR / "results"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
TRANSLATIONS_FILE = RESULTS_DIR / "translated_queries.json"
MANIFEST_FILE = RESULTS_DIR / "run_manifest.json"


def load_variants() -> list[dict[str, Any]]:
    """Load gold standard variants."""
    variants: list[dict[str, Any]] = []
    with open(GOLD_STANDARD_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                variants.append(json.loads(line))
    return variants


# ── Collect phase ────────────────────────────────────────────────────────


async def _citation_extension(
    base: list[dict[str, Any]],
    cg_budget: int,
    cg_depth: int,
) -> list[dict[str, Any]]:
    """Traverse the citation graph seeded from a FROZEN candidate list.

    Returns only the new (deduplicated) items to append; the base list is
    not modified or re-retrieved.
    """
    from lit_acquisition import traverse_citation_graph
    from lit_acquisition.normalize import normalize_items

    seed_doi = next((p.get("doi") for p in base if p.get("doi")), None)
    if not seed_doi:
        return []

    cited = await traverse_citation_graph(
        seed=seed_doi,
        max_depth=cg_depth,
        max_papers=cg_budget,
        direction="both",
    )

    seen = {pid for p in base if (pid := paper_id(p))}
    appended: list[dict[str, Any]] = []
    for paper in cited:
        for item in normalize_items("semantic_scholar", [paper]):
            d = item.model_dump()
            d["method"] = "full_pipeline"
            d["source"] = "citation_graph"
            pid = paper_id(d)
            if pid and pid in seen:
                continue
            if pid:
                seen.add(pid)
            appended.append(d)
    return appended


async def collect_results(limit: int = 50, cg_budget: int = 20, cg_depth: int = 1) -> None:
    """Run all methods on all variants with a uniform candidate budget."""
    from baselines.pubmed_only import search_pubmed_only
    from baselines.multisource_no_translation import search_multisource_no_translation
    from baselines.multisource_no_citation import search_multisource_with_translation

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    variants = load_variants()
    print(f"Loaded {len(variants)} variants from gold standard")

    started_at = datetime.now(timezone.utc).isoformat()
    all_results: dict[str, dict[str, list[dict[str, Any]]]] = {}
    timings: dict[str, dict[str, float]] = {}
    translations_log: dict[str, dict[str, str]] = {}

    # ── M1-M3: independent retrieval runs, identical budgets ──
    async def run_m1(v: dict[str, Any]) -> list[dict[str, Any]]:
        found = await search_pubmed_only(v["query"], limit=limit)
        for p in found:
            p["method"] = "pubmed_only"
        return found

    async def run_m2(v: dict[str, Any]) -> list[dict[str, Any]]:
        return await search_multisource_no_translation(v["query"], limit=limit)

    async def run_m3(v: dict[str, Any]) -> list[dict[str, Any]]:
        found, translations = await search_multisource_with_translation(v["query"], limit=limit)
        translations_log[v["variant_id"]] = translations
        return found

    for method_name, run_fn in [
        ("pubmed_only", run_m1),
        ("multisource_no_translation", run_m2),
        ("multisource_with_translation", run_m3),
    ]:
        print(f"\n{'='*60}\nRunning method: {method_name}\n{'='*60}")
        method_results: dict[str, list[dict[str, Any]]] = {}
        method_times: dict[str, float] = {}
        for variant in variants:
            vid = variant["variant_id"]
            start = time.monotonic()
            found = await run_fn(variant)
            method_times[vid] = round(time.monotonic() - start, 2)
            method_results[vid] = found
            print(f"  [{method_name}] {vid}: {len(found)} papers ({method_times[vid]:.1f}s)")
        all_results[method_name] = method_results
        timings[method_name] = method_times
        total = sum(len(v) for v in method_results.values())
        print(f"  {method_name}: {total} papers in {sum(method_times.values()):.1f}s")

    # ── M4: STRICTLY NESTED extension of the frozen M3 lists ──
    print(f"\n{'='*60}\nRunning method: full_pipeline (nested on frozen M3)\n{'='*60}")
    m3_results = all_results["multisource_with_translation"]
    full_results: dict[str, list[dict[str, Any]]] = {}
    m4_times: dict[str, float] = {}
    for variant in variants:
        vid = variant["variant_id"]
        base = copy.deepcopy(m3_results[vid])
        for p in base:
            p["method"] = "full_pipeline"
        start = time.monotonic()
        try:
            appended = await _citation_extension(base, cg_budget=cg_budget, cg_depth=cg_depth)
        except Exception as exc:
            print(f"  citation graph failed for {vid}: {exc}")
            appended = []
        m4_times[vid] = round(time.monotonic() - start, 2)
        full_results[vid] = base + appended
        print(f"  [full_pipeline] {vid}: {len(base)}+{len(appended)} papers ({m4_times[vid]:.1f}s)")
    all_results["full_pipeline"] = full_results
    timings["full_pipeline_citation_phase"] = m4_times

    # ── Persist raw lists, translations, manifest ──
    with open(COLLECTED_FILE, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nCollected results saved to {COLLECTED_FILE}")

    with open(TRANSLATIONS_FILE, "w") as f:
        json.dump(translations_log, f, ensure_ascii=False, indent=2)
    print(f"Translated queries saved to {TRANSLATIONS_FILE}")

    manifest = _build_manifest(started_at, limit, cg_budget, cg_depth, all_results, timings)
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"Run manifest saved to {MANIFEST_FILE}")

    for method, results in all_results.items():
        total = sum(len(v) for v in results.values())
        print(f"  {method}: {total} total papers across {len(results)} variants")


def _build_manifest(
    started_at: str,
    limit: int,
    cg_budget: int,
    cg_depth: int,
    all_results: dict[str, dict[str, list[dict[str, Any]]]],
    timings: dict[str, dict[str, float]],
) -> dict[str, Any]:
    """Record everything needed to interpret/reproduce this collection run.

    Model names and boolean key-presence flags only; no secrets.
    """
    from lit_acquisition.config import get_config
    from lit_acquisition.query_translator import _SYSTEM_PROMPT

    cfg = get_config()
    return {
        "started_at_utc": started_at,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "command_args": {"limit": limit, "cg_budget": cg_budget, "cg_depth": cg_depth},
        "protocol": {
            "budget": f"{limit} candidates per variant for every method",
            "m4_construction": "frozen M3 list + citation traversal (nested; "
            "keyword phase not re-executed)",
            "citation_traversal": {
                "seed": "first DOI of the frozen M3 list",
                "max_depth": cg_depth,
                "max_papers": cg_budget,
                "direction": "both",
            },
            "relevance_gate": "disabled during collection",
            "plans": "en/auto include the PubMed E-utilities channel",
            "rerank": {
                "enabled": rerank_enabled(),
                "model": os.getenv("LIT_RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
                "note": "cross-encoder final ranking stage when enabled",
            },
        },
        "llm": {
            "translation_model": cfg.translation.model or cfg.llm.model or None,
            "translation_temperature": cfg.translation.temperature,
            "translation_prompt_sha256_16": hashlib.sha256(
                _SYSTEM_PROMPT.encode()
            ).hexdigest()[:16],
        },
        "provider_keys_present": {
            "pubmed": bool(cfg.pubmed.api_key),
            "semantic_scholar": bool(cfg.semantic_scholar.api_key),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "per_method_totals": {
            m: sum(len(v) for v in res.values()) for m, res in all_results.items()
        },
        "per_variant_wall_s": timings,
    }


# ── Compute phase ────────────────────────────────────────────────────────


def compute_metrics() -> None:
    """Regenerate metrics + paper data deterministically from raw lists."""
    if not COLLECTED_FILE.exists():
        print(f"Error: {COLLECTED_FILE} not found. Run --collect first.")
        sys.exit(1)
    import make_paper_data

    make_paper_data.main()


# ── Analyze phase ────────────────────────────────────────────────────────


def run_analysis() -> None:
    """Print analysis tables (source coverage, list integrity, performance)."""
    from analysis.coverage import analyze_source_coverage
    from analysis.dedup_eval import analyze_deduplication
    from analysis.performance import analyze_performance

    if not COLLECTED_FILE.exists():
        print(f"Error: {COLLECTED_FILE} not found. Run --collect first.")
        sys.exit(1)

    with open(COLLECTED_FILE) as f:
        all_results = json.load(f)

    variants = load_variants()

    print("\n" + "=" * 80)
    print("Source Coverage Analysis")
    print("=" * 80)
    analyze_source_coverage(all_results, variants)

    print("\n" + "=" * 80)
    print("Candidate List Integrity")
    print("=" * 80)
    analyze_deduplication(all_results)

    print("\n" + "=" * 80)
    print("Performance Analysis")
    print("=" * 80)
    analyze_performance(all_results)


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark runner for lit-acquisition")
    parser.add_argument("--collect", action="store_true", help="Run all methods, collect results")
    parser.add_argument("--compute", action="store_true", help="Compute metrics + paper data")
    parser.add_argument("--analyze", action="store_true", help="Print analysis tables")
    parser.add_argument("--all", action="store_true", help="Run all steps")
    parser.add_argument("--limit", type=int, default=50,
                        help="Candidate budget per variant, all methods (default 50)")
    parser.add_argument("--cg-budget", type=int, default=20,
                        help="Citation traversal paper budget (default 20)")
    parser.add_argument("--cg-depth", type=int, default=1,
                        help="Citation traversal depth (default 1)")
    args = parser.parse_args()

    if args.all:
        args.collect = True
        args.compute = True
        args.analyze = True

    if args.collect:
        asyncio.run(collect_results(limit=args.limit, cg_budget=args.cg_budget,
                                    cg_depth=args.cg_depth))

    if args.compute:
        compute_metrics()

    if args.analyze:
        run_analysis()


if __name__ == "__main__":
    main()
