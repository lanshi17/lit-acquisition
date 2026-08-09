"""Deduplication evaluation.

Evaluates the effectiveness of the DOI/URL/title-based deduplication
by measuring how many duplicates are caught and whether any false
merges occur.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


def _norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    return doi.strip().lower().lstrip("https://doi.org/").lstrip("http://doi.org/")


def _norm_title(title: str | None) -> str:
    if not title:
        return ""
    import re
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def analyze_deduplication(all_results: dict[str, dict[str, list[dict[str, Any]]]]) -> None:
    """Analyze deduplication effectiveness.

    Args:
        all_results: {method: {variant_id: [paper_dicts]}}
    """
    # Compare raw results (before dedup) vs deduped results
    # For this, we look at how many papers have the same DOI/title
    # within each method's results

    for method, method_results in all_results.items():
        total_papers = 0
        unique_doi = set()
        unique_title = set()
        duplicate_doi = 0
        duplicate_title = 0

        per_variant_dupes: list[int] = []

        for variant_id, papers in method_results.items():
            total_papers += len(papers)
            seen_doi: set[str] = set()
            seen_title: set[str] = set()
            dupe_count = 0

            for paper in papers:
                doi = _norm_doi(paper.get("doi"))
                title = _norm_title(paper.get("title"))

                if doi:
                    if doi in seen_doi:
                        duplicate_doi += 1
                        dupe_count += 1
                    else:
                        seen_doi.add(doi)
                        unique_doi.add(doi)

                if title:
                    if title in seen_title:
                        duplicate_title += 1
                    else:
                        seen_title.add(title)
                        unique_title.add(title)

            per_variant_dupes.append(dupe_count)

        unique_count = len(unique_doi) + len([
            t for t in unique_title
            if t and not any(_norm_doi(d) in unique_doi for d in [])
        ])

        print(f"\n{method}:")
        print(f"  Total papers returned: {total_papers}")
        print(f"  Unique DOIs: {len(unique_doi)}")
        print(f"  Duplicate DOIs caught: {duplicate_doi}")
        print(f"  Unique titles: {len(unique_title)}")
        print(f"  Duplicate titles caught: {duplicate_title}")
        if total_papers > 0:
            dedup_rate = (duplicate_doi + duplicate_title) / total_papers * 100
            print(f"  Estimated dedup rate: {dedup_rate:.1f}%")

    # Cross-method dedup: how many papers are found by multiple methods?
    print("\n\nCross-method overlap:")
    paper_methods: dict[str, set[str]] = defaultdict(set)
    for method, method_results in all_results.items():
        for variant_id, papers in method_results.items():
            for paper in papers:
                doi = _norm_doi(paper.get("doi"))
                if doi:
                    paper_methods[doi].add(method)

    total_papers = len(paper_methods)
    method_overlap = defaultdict(int)
    for doi, methods in paper_methods.items():
        if len(methods) > 1:
            method_overlap[frozenset(methods)] += 1

    print(f"  Total unique papers (by DOI): {total_papers}")
    print(f"  Found by multiple methods: {sum(method_overlap.values())}")
    for methods, count in sorted(method_overlap.items(), key=lambda x: -x[1]):
        print(f"    {' + '.join(sorted(methods))}: {count}")

    output = {
        "total_unique_papers": total_papers,
        "multi_method_papers": sum(method_overlap.values()),
        "method_overlap": {str(k): v for k, v in method_overlap.items()},
    }
    with open("results/dedup_analysis.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved to results/dedup_analysis.json")
