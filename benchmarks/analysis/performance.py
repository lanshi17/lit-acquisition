"""Performance analysis.

Measures latency, throughput, and scalability of the acquisition pipeline.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from typing import Any


def analyze_performance(all_results: dict[str, dict[str, list[dict[str, Any]]]]) -> None:
    """Analyze performance characteristics of each method.

    Since we don't have per-method timing in the collected results,
    this function provides a framework for measuring performance.
    Run with --collect to time each method.

    Args:
        all_results: {method: {variant_id: [paper_dicts]}}
    """
    # Paper count statistics per method
    print("\nPaper Count Statistics:")
    print(f"{'Method':<35} {'Total':>8} {'Mean':>8} {'Min':>8} {'Max':>8}")
    print("-" * 70)

    method_stats: dict[str, dict[str, Any]] = {}
    for method, method_results in all_results.items():
        counts = [len(papers) for papers in method_results.values()]
        if not counts:
            continue
        total = sum(counts)
        mean = total / len(counts)
        stats = {
            "total": total,
            "mean": mean,
            "min": min(counts),
            "max": max(counts),
            "n_variants": len(counts),
        }
        method_stats[method] = stats
        print(f"{method:<35} {total:>8} {mean:>8.1f} {min(counts):>8} {max(counts):>8}")

    # Unique paper ratio (how many unique papers vs total returned)
    print("\n\nUnique Paper Ratio:")
    print(f"{'Method':<35} {'Total':>8} {'Unique':>8} {'Ratio':>8}")
    print("-" * 62)
    for method, method_results in all_results.items():
        all_dois: set[str] = set()
        total = 0
        for papers in method_results.values():
            for paper in papers:
                total += 1
                doi = (paper.get("doi") or "").strip().lower()
                if doi:
                    all_dois.add(doi)
        ratio = len(all_dois) / total if total > 0 else 0
        print(f"{method:<35} {total:>8} {len(all_dois):>8} {ratio:>8.2%}")

    # Per-variant breakdown (for finding outliers)
    print("\n\nPer-Variant Paper Counts (full_pipeline):")
    full_results = all_results.get("full_pipeline", {})
    for variant_id, papers in sorted(full_results.items()):
        unique = len({(p.get("doi") or "").lower() for p in papers if p.get("doi")})
        print(f"  {variant_id}: {len(papers)} papers ({unique} unique DOIs)")

    output = {
        "method_stats": method_stats,
    }
    with open("results/performance_analysis.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved to results/performance_analysis.json")
