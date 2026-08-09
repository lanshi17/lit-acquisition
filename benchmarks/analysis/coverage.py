"""Source coverage analysis.

Analyzes which data sources contribute relevant papers and how much
overlap exists between sources. This is a key section for the JCDL paper.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


def analyze_source_coverage(
    all_results: dict[str, dict[str, list[dict[str, Any]]]],
    variants: list[dict[str, Any]],
) -> None:
    """Analyze per-source contribution and overlap.

    Args:
        all_results: {method: {variant_id: [paper_dicts]}}
        variants: Gold standard variants list.
    """
    # Use the full_pipeline results for coverage analysis
    full_results = all_results.get("full_pipeline") or all_results.get("multisource_with_translation") or {}

    # Per-source paper counts
    source_counts: dict[str, int] = defaultdict(int)
    source_by_variant: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for variant_id, papers in full_results.items():
        for paper in papers:
            source = paper.get("source") or paper.get("_source_provider") or "unknown"
            source_counts[source] += 1
            source_by_variant[variant_id][source] += 1

    # Print per-source summary
    print("\nSource Contribution (full_pipeline):")
    print(f"{'Source':<25} {'Total Papers':>12} {'Variants Covered':>16}")
    print("-" * 55)
    for source, count in sorted(source_counts.items(), key=lambda x: -x[1]):
        variants_covered = sum(1 for v in source_by_variant.values() if v.get(source, 0) > 0)
        print(f"{source:<25} {count:>12} {variants_covered:>16}")

    # Source overlap matrix (which sources find the same papers)
    # Build paper -> sources mapping
    paper_sources: dict[str, set[str]] = defaultdict(set)
    for variant_id, papers in full_results.items():
        for paper in papers:
            doi = (paper.get("doi") or "").strip().lower()
            if doi:
                source = paper.get("source") or "unknown"
                paper_sources[doi].add(source)

    # Unique papers per source (papers only found by that source)
    unique_papers: dict[str, int] = defaultdict(int)
    for doi, sources in paper_sources.items():
        if len(sources) == 1:
            unique_papers[next(iter(sources))] += 1

    print("\nUnique Papers (found by only one source):")
    print(f"{'Source':<25} {'Unique Papers':>14}")
    print("-" * 40)
    for source, count in sorted(unique_papers.items(), key=lambda x: -x[1]):
        print(f"{source:<25} {count:>14}")

    # Overlap summary
    total_unique = len(paper_sources)
    multi_source = sum(1 for s in paper_sources.values() if len(s) > 1)
    print(f"\nTotal unique papers: {total_unique}")
    print(f"Found by multiple sources: {multi_source} ({multi_source/total_unique*100:.1f}%)" if total_unique else "")

    # Save for paper
    output = {
        "source_counts": dict(source_counts),
        "unique_papers": dict(unique_papers),
        "total_unique": total_unique,
        "multi_source": multi_source,
        "source_by_variant": {k: dict(v) for k, v in source_by_variant.items()},
    }
    output_path = "results/source_coverage.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved to {output_path}")
