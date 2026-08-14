"""Source coverage analysis.

Analyzes which data sources contribute papers under FIRST-SEEN attribution:
each deduplicated record carries the label of the first provider that
surfaced it during collection. Because deduplication removes any later
occurrences from other providers, per-source overlap CANNOT be measured
from these lists -- a zero-overlap matrix is a tautological consequence of
deduplication, not evidence that sources are complementary.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).parent.parent / "results"


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
    print("\nSource Contribution (full_pipeline, FIRST-SEEN attribution):")
    print(f"{'Source':<25} {'Total Papers':>12} {'Variants Covered':>16}")
    print("-" * 55)
    for source, count in sorted(source_counts.items(), key=lambda x: -x[1]):
        variants_covered = sum(1 for v in source_by_variant.values() if v.get(source, 0) > 0)
        print(f"{source:<25} {count:>12} {variants_covered:>16}")

    # Unique DOIs per first-seen source label.
    paper_sources: dict[str, set[str]] = defaultdict(set)
    for variant_id, papers in full_results.items():
        for paper in papers:
            doi = (paper.get("doi") or "").strip().lower()
            if doi:
                source = paper.get("source") or "unknown"
                paper_sources[doi].add(source)
    unique_dois_per_source: dict[str, int] = defaultdict(int)
    for doi, sources in paper_sources.items():
        if len(sources) == 1:
            unique_dois_per_source[next(iter(sources))] += 1

    print("\nUnique DOIs per first-seen source label:")
    print(f"{'Source':<25} {'Unique DOIs':>14}")
    print("-" * 40)
    for source, count in sorted(unique_dois_per_source.items(), key=lambda x: -x[1]):
        print(f"{source:<25} {count:>14}")

    print(
        "\nNote: labels are first-seen attribution on deduplicated lists. "
        "Cross-source availability overlap is unmeasurable here; do NOT "
        "interpret label disjointness as source complementarity."
    )

    # Save for paper
    output = {
        "attribution": "first-seen (post-deduplication)",
        "source_counts": dict(source_counts),
        "unique_dois_per_source_label": dict(unique_dois_per_source),
        "source_by_variant": {k: dict(v) for k, v in source_by_variant.items()},
    }
    output_path = RESULTS_DIR / "source_coverage.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved to {output_path}")
