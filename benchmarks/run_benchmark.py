"""Main benchmark runner.

Usage:
    python run_benchmark.py --collect      # Run all methods, collect results
    python run_benchmark.py --compute      # Compute metrics from collected results
    python run_benchmark.py --analyze      # Generate analysis tables and figures
    python run_benchmark.py --all          # Run all steps
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Add parent dir to path for lit_acquisition import
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Add benchmarks dir to path for local imports
sys.path.insert(0, str(Path(__file__).parent))

from metrics import (
    VariantMetrics,
    aggregate_metrics,
    compute_variant_metrics,
    format_summary_table,
)

BENCHMARK_DIR = Path(__file__).parent
GOLD_STANDARD_FILE = BENCHMARK_DIR / "gold_standard" / "variants.jsonl"
RESULTS_DIR = BENCHMARK_DIR / "results"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
ANNOTATED_FILE = RESULTS_DIR / "annotated_results.json"
METRICS_FILE = RESULTS_DIR / "metrics.json"
SUMMARY_FILE = RESULTS_DIR / "summary.md"


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


async def collect_results(limit: int = 20) -> None:
    """Run all methods on all variants, save collected results."""
    from baselines.pubmed_only import run_baseline as run_pubmed
    from baselines.multisource_no_translation import run_baseline as run_no_trans
    from baselines.multisource_no_citation import run_baseline as run_with_trans

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    variants = load_variants()
    print(f"Loaded {len(variants)} variants from gold standard")

    all_results: dict[str, dict[str, list[dict[str, Any]]]] = {}

    # Run each method
    methods = [
        ("pubmed_only", run_pubmed),
        ("multisource_no_translation", run_no_trans),
        ("multisource_with_translation", run_with_trans),
    ]

    for method_name, run_fn in methods:
        print(f"\n{'='*60}")
        print(f"Running method: {method_name}")
        print(f"{'='*60}")
        start = time.monotonic()
        results = await run_fn(variants, limit=limit)
        elapsed = time.monotonic() - start
        total = sum(len(v) for v in results.values())
        print(f"  {method_name}: {total} papers in {elapsed:.1f}s")
        all_results[method_name] = results

    # Also run full pipeline (with citation graph)
    print(f"\n{'='*60}")
    print("Running method: full_pipeline")
    print(f"{'='*60}")
    from lit_acquisition import multilingual_acquisition_workflow, traverse_citation_graph

    full_results: dict[str, list[dict[str, Any]]] = {}
    for variant in variants:
        query = variant["query"]
        start = time.monotonic()

        # Run multilingual search
        result = await multilingual_acquisition_workflow({
            "query": query,
            "action": "search",
            "limit": limit,
            "language": "auto",
            "relevance_gate": False,
        })

        found: list[dict[str, Any]] = []
        for item in result.get("items", []):
            item["method"] = "full_pipeline"
            found.append(item)

        # Citation graph augmentation: use first DOI as seed
        if found:
            first_doi = found[0].get("doi")
            if first_doi:
                try:
                    cited = await traverse_citation_graph(
                        seed=first_doi,
                        max_depth=1,
                        max_papers=20,
                        direction="both",
                    )
                    from lit_acquisition.normalizers import normalize_items
                    for paper in cited:
                        normalized = normalize_items("semantic_scholar", [paper])
                        for item in normalized:
                            d = item.model_dump()
                            d["method"] = "full_pipeline"
                            d["source"] = "citation_graph"
                            found.append(d)
                except Exception as exc:
                    print(f"  citation graph failed for {variant['variant_id']}: {exc}")

        elapsed = time.monotonic() - start
        full_results[variant["variant_id"]] = found
        print(f"  [full_pipeline] {variant['variant_id']}: {len(found)} papers ({elapsed:.1f}s)")

    all_results["full_pipeline"] = full_results

    # Save collected results
    with open(COLLECTED_FILE, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nCollected results saved to {COLLECTED_FILE}")

    # Print summary
    for method, results in all_results.items():
        total = sum(len(v) for v in results.values())
        print(f"  {method}: {total} total papers across {len(results)} variants")


# ── Compute metrics phase ────────────────────────────────────────────────


def compute_metrics(k: int = 50) -> None:
    """Compute metrics from collected results and gold standard."""
    if not COLLECTED_FILE.exists():
        print(f"Error: {COLLECTED_FILE} not found. Run --collect first.")
        sys.exit(1)

    with open(COLLECTED_FILE) as f:
        all_results = json.load(f)

    variants = load_variants()
    variants_by_id = {v["variant_id"]: v for v in variants}

    all_metrics: list[VariantMetrics] = []
    for method, method_results in all_results.items():
        for variant_id, found_papers in method_results.items():
            variant = variants_by_id.get(variant_id)
            if not variant:
                continue
            metrics = compute_variant_metrics(variant, found_papers, k=k)
            all_metrics.append(metrics)

    # Save detailed metrics
    metrics_data = [m.__dict__ for m in all_metrics]
    with open(METRICS_FILE, "w") as f:
        json.dump(metrics_data, f, ensure_ascii=False, indent=2)

    # Aggregate and print summary
    summary = aggregate_metrics(all_metrics)
    table = format_summary_table(summary)
    print("\n" + "=" * 80)
    print(f"Results (K={k})")
    print("=" * 80)
    print(table)

    with open(SUMMARY_FILE, "w") as f:
        f.write(f"# Benchmark Results (K={k})\n\n")
        f.write(table + "\n\n")
        for method, stats in summary.items():
            f.write(f"\n## {method}\n")
            f.write(f"- Variants: {stats['n_variants']}\n")
            f.write(f"- Recall: {stats['recall_mean']:.3f} ± {stats['recall_std']:.3f}\n")
            f.write(f"- Precision: {stats['precision_mean']:.3f} ± {stats['precision_std']:.3f}\n")
            f.write(f"- F1: {stats['f1_mean']:.3f} ± {stats['f1_std']:.3f}\n")
            f.write(f"- nDCG: {stats['ndcg_mean']:.3f} ± {stats['ndcg_std']:.3f}\n")
            f.write(f"- Relevant found: {stats['total_relevant_found']}/{stats['total_relevant']}\n")
            if stats.get("source_coverage"):
                f.write(f"- Source coverage: {stats['source_coverage']}\n")
            if stats.get("language_coverage"):
                f.write(f"- Language coverage: {stats['language_coverage']}\n")

    print(f"\nSummary saved to {SUMMARY_FILE}")
    print(f"Detailed metrics saved to {METRICS_FILE}")


# ── Analyze phase ────────────────────────────────────────────────────────


def run_analysis() -> None:
    """Generate analysis tables and figures."""
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
    print("Deduplication Analysis")
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
    parser.add_argument("--compute", action="store_true", help="Compute metrics from results")
    parser.add_argument("--analyze", action="store_true", help="Generate analysis")
    parser.add_argument("--all", action="store_true", help="Run all steps")
    parser.add_argument("--limit", type=int, default=20, help="Max results per variant")
    parser.add_argument("--k", type=int, default=50, help="K for Recall@K metrics")
    args = parser.parse_args()

    if args.all:
        args.collect = True
        args.compute = True
        args.analyze = True

    if args.collect:
        asyncio.run(collect_results(limit=args.limit))

    if args.compute:
        compute_metrics(k=args.k)

    if args.analyze:
        run_analysis()


if __name__ == "__main__":
    main()
