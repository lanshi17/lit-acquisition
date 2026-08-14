"""Candidate-list integrity analysis.

Reports what the recorded lists actually allow us to measure about
deduplication:

- within-variant duplicate identities remaining AFTER the pipeline's
  DOI/URL/title deduplication (its residual error, ideally 0);
- records lacking a DOI (metadata completeness);
- cross-variant repeated identities (the same paper legitimately returned
  for several variant queries -- expected behavior, NOT duplication).

It does NOT estimate deduplication precision/recall: that requires the raw
pre-deduplication provider streams, which collection runs do not persist.
Consequently, a post-deduplication zero-overlap matrix between sources must
not be read as evidence of source complementarity.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from metrics import paper_id

RESULTS_DIR = Path(__file__).parent.parent / "results"


def analyze_deduplication(all_results: dict[str, dict[str, list[dict[str, Any]]]]) -> None:
    """Analyze candidate list integrity per method.

    Args:
        all_results: {method: {variant_id: [paper_dicts]}}
    """
    output: dict[str, Any] = {}
    print(f"\n{'Method':<32} {'Total':>7} {'InVarDup':>9} {'NoDOI':>7} {'XVarRep':>8}")
    print("-" * 68)
    for method, method_results in all_results.items():
        total = 0
        within_dupes = 0
        missing_doi = 0
        counter: Counter[str] = Counter()
        for papers in method_results.values():
            seen: set[str] = set()
            for p in papers:
                total += 1
                if not p.get("doi"):
                    missing_doi += 1
                pid = paper_id(p)
                if not pid:
                    continue
                if pid in seen:
                    within_dupes += 1
                else:
                    seen.add(pid)
                    counter[pid] += 1
        cross_repeats = sum(n - 1 for n in counter.values() if n > 1)
        output[method] = {
            "total_entries": total,
            "within_variant_duplicate_ids": within_dupes,
            "missing_doi": missing_doi,
            "cross_variant_repeated_ids": cross_repeats,
        }
        print(f"{method:<32} {total:>7} {within_dupes:>9} {missing_doi:>7} {cross_repeats:>8}")

    print(
        "\nNote: within-variant duplicates measure residual dedup error; "
        "cross-variant repeats are expected (same paper relevant to several "
        "queries). Dedup precision/recall would require pre-dedup provider "
        "streams, which are not persisted."
    )

    out_path = RESULTS_DIR / "dedup_analysis.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved to {out_path}")
