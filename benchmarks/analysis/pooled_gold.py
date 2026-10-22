"""Pooled reference-set expansion (TREC-style pooling on the project's own rule).

The 822-paper reference set was built by scoring a *fixed* search pool
(PubMed + EuropePMC + Crossref, capped) with the documented heuristic of
``gold_standard/build_variants.py``: gene +3, variant +5, disease keyword +1,
threshold > 4. Any paper whose title contains the variant string therefore
scores at least 5 and is relevant *by the project's own rule* --- but papers
that were not in the 2026-08-14 pool never got scored.

The frozen benchmark artifacts now contain ten systems' candidate lists for the
same 43 variants, a far larger pool. Re-applying the identical rule to that pool
recovers papers the reference set is missing. Because every system (M1, M2, M3,
M4n and the six SOTA baselines) contributes to the pool, the pooled judgments
are not biased toward a single system --- this is the standard TREC pooling
argument, and the same caveat applies as in the original set: judgments come
from titles, not from full-text assessment.

This module is imported by the offline analyses (E1 deconfounding, E3 fair-pool
SOTA) so that every method is measured on both the frozen reference set and the
expanded one.

Public API
----------
``pooled_additions(gold, pools)``
    variant_id -> list of newly judged relevant papers (deduplicated against
    the frozen set, with the heuristic reason string attached).
``expanded_gold(gold, pools)``
    ``(copy_of_gold_with_additions, report)``.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from gold_standard.build_variants import DISEASE_KEYWORDS, _score_title  # noqa: E402
from metrics import paper_id  # noqa: E402

THRESHOLD = 4  # documented in build_variants.py (--threshold default)


def candidate_pool(
    pools: dict[str, dict[str, list[dict[str, Any]]]],
    variant_id: str,
) -> dict[str, dict[str, Any]]:
    """Union of every system's candidates for one variant, keyed by paper id."""
    merged: dict[str, dict[str, Any]] = {}
    for lists in pools.values():
        for paper in lists.get(variant_id) or []:
            pid = paper_id(paper)
            if pid and pid not in merged:
                merged[pid] = paper
    return merged


def pooled_additions(
    gold: dict[str, dict[str, Any]],
    pools: dict[str, dict[str, list[dict[str, Any]]]],
    *,
    threshold: int = THRESHOLD,
    require_variant_hit: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Papers the documented rule judges relevant but the frozen set lacks."""
    additions: dict[str, list[dict[str, Any]]] = {}
    for vid, variant in gold.items():
        existing = {paper_id(p) for p in variant.get("relevant_papers") or []}
        keywords = DISEASE_KEYWORDS.get(variant["gene"], [])
        found = []
        for pid, paper in candidate_pool(pools, vid).items():
            if pid in existing:
                continue
            score, reason = _score_title(
                paper.get("title") or "", variant["gene"], variant["variant"], keywords, []
            )
            if score <= threshold:
                continue
            if require_variant_hit and "variant" not in reason:
                continue
            entry = copy.deepcopy(paper)
            entry["evidence_type"] = "pooled"
            entry["annotation_reason"] = reason
            entry["pooled_score"] = score
            found.append(entry)
        if found:
            additions[vid] = found
    return additions


def expanded_gold(
    gold: dict[str, dict[str, Any]],
    pools: dict[str, dict[str, list[dict[str, Any]]]],
    *,
    threshold: int = THRESHOLD,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return (expanded gold copy, report)."""
    additions = pooled_additions(gold, pools, threshold=threshold)
    expanded = copy.deepcopy(gold)
    for vid, papers in additions.items():
        expanded[vid]["relevant_papers"].extend(papers)
    before = sum(len(v.get("relevant_papers") or []) for v in gold.values())
    after = sum(len(v.get("relevant_papers") or []) for v in expanded.values())
    per_variant = {
        vid: {
            "frozen": len(gold[vid].get("relevant_papers") or []),
            "added": len(papers),
            "expanded": len(gold[vid].get("relevant_papers") or []) + len(papers),
        }
        for vid, papers in sorted(additions.items())
    }
    report = {
        "rule": (
            "gold_standard/build_variants.py heuristic (gene +3, variant +5, disease keyword +1) with "
            f"threshold > {threshold} and a required variant-string hit, applied to the pooled candidate "
            "lists of all ten recorded systems"
        ),
        "frozen_total": before,
        "added_total": after - before,
        "expanded_total": after,
        "variants_with_additions": len(additions),
        "per_variant": per_variant,
        "caveats": (
            "Pooled judgments come from titles only, exactly like the frozen set, and every system "
            "contributes to the pool (so no single system is favoured); the rule is the project's own, not "
            "an external gold standard, and human adjudication (plan §2.3) remains necessary."
        ),
    }
    return expanded, report


def total_relevant(gold: dict[str, dict[str, Any]]) -> int:
    return sum(len(v.get("relevant_papers") or []) for v in gold.values())


def dump_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))


__all__ = ["candidate_pool", "pooled_additions", "expanded_gold", "total_relevant", "dump_report", "THRESHOLD"]
