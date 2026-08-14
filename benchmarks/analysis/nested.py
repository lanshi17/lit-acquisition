"""Strictly nested ablation construction.

The original ``full_pipeline`` collection re-ran the keyword phase (a fresh
M3-configuration retrieval) before appending citation-graph candidates.
Live-API variance therefore leaked into the M3-vs-M4 comparison: 12 of 30
variants ended up with keyword lists that differ from the frozen M3 run,
which is why the recorded M4 can show LOWER recall than M3 on some variants
even though it is nominally a superset method.

This module derives the protocol-correct nested variant offline from the
recorded raw candidate lists:

    M4_nested(variant) = frozen M3 ranked list
                         + citation-graph items recorded in the M4 run
                           (deduplicated against the M3 list, appended after
                           the keyword candidates, original traversal order)

Because candidates are only appended, Recall@K(M4_nested) >= Recall@K(M3)
holds for every variant and every K by construction; the module verifies
this monotonicity property explicitly.

It also quantifies run-to-run variance: the M3 run and the keyword phase of
the M4 run are two independent executions of the *same* configuration
(identical queries, providers, budgets) minutes apart, so their disagreement
is a direct estimate of live-API retrieval variance.
"""

from __future__ import annotations

import copy
from typing import Any

from metrics import compute_set_recall, paper_id

NESTED_METHOD = "full_pipeline_nested"
CITATION_SOURCE = "citation_graph"


def split_full_pipeline(
    m4_papers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a recorded full-pipeline list into (keyword phase, citation phase)."""
    keyword = [p for p in m4_papers if p.get("source") != CITATION_SOURCE]
    citation = [p for p in m4_papers if p.get("source") == CITATION_SOURCE]
    return keyword, citation


def build_nested_full_pipeline(
    collected: dict[str, dict[str, list[dict[str, Any]]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Construct the nested M4 lists and a provenance/verification report.

    Returns:
        (nested_results, report) where nested_results maps variant_id to the
        nested candidate list and report records per-variant construction
        details (seed provenance, appended/duplicate counts).
    """
    m3_all = collected["multisource_with_translation"]
    m4_all = collected["full_pipeline"]

    nested: dict[str, list[dict[str, Any]]] = {}
    per_variant: dict[str, Any] = {}

    for vid in sorted(m3_all):
        m3_list = m3_all[vid]
        m4_keyword, m4_citation = split_full_pipeline(m4_all.get(vid, []))

        base = copy.deepcopy(m3_list)
        for p in base:
            p["method"] = NESTED_METHOD
        seen = {pid for p in base if (pid := paper_id(p))}

        appended: list[dict[str, Any]] = []
        n_dupe = 0
        for p in m4_citation:
            pid = paper_id(p)
            if pid and pid in seen:
                n_dupe += 1
                continue
            if pid:
                seen.add(pid)
            q = copy.deepcopy(p)
            q["method"] = NESTED_METHOD
            appended.append(q)

        nested[vid] = base + appended

        # Seed provenance: the recorded traversal was seeded with the first
        # DOI of the M4 keyword list; check whether the frozen M3 list would
        # have produced the same seed.
        def first_doi(papers: list[dict[str, Any]]) -> str | None:
            return next(
                ((p.get("doi") or "").strip().lower() for p in papers if p.get("doi")),
                None,
            )

        per_variant[vid] = {
            "n_m3": len(m3_list),
            "n_citation_recorded": len(m4_citation),
            "n_citation_appended": len(appended),
            "n_citation_dupe_vs_m3": n_dupe,
            "seed_matches_m3": first_doi(m4_keyword) == first_doi(m3_list),
        }

    report = {
        "method": NESTED_METHOD,
        "construction": (
            "frozen M3 ranked list + citation-graph items recorded in the "
            "independent M4 run, deduplicated against M3 and appended in "
            "traversal order"
        ),
        "per_variant": per_variant,
        "n_variants_with_citation_items": sum(
            1 for v in per_variant.values() if v["n_citation_appended"] > 0
        ),
        "n_seed_matches_m3": sum(1 for v in per_variant.values() if v["seed_matches_m3"]),
    }
    return nested, report


def verify_monotonicity(
    m3_results: dict[str, list[dict[str, Any]]],
    nested_results: dict[str, list[dict[str, Any]]],
    variants_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Check per-variant SetRecall(M4_nested) >= SetRecall(M3).

    Because the nested list extends the frozen M3 list without reordering,
    Recall@K monotonicity for every K follows from set-level monotonicity.
    """
    violations: list[str] = []
    deltas: dict[str, float] = {}
    for vid, m3_list in m3_results.items():
        gold = variants_by_id[vid].get("relevant_papers") or []
        r3 = compute_set_recall(m3_list, gold)
        r4 = compute_set_recall(nested_results[vid], gold)
        deltas[vid] = r4 - r3
        if r4 < r3 - 1e-12:
            violations.append(vid)
    return {
        "holds": not violations,
        "violations": violations,
        "n_variants_improved": sum(1 for d in deltas.values() if d > 1e-12),
        "per_variant_delta_set_recall": deltas,
    }


def run_variance_analysis(
    collected: dict[str, dict[str, list[dict[str, Any]]]],
    variants_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Quantify run-to-run live-API variance from the two M3-config runs.

    The ``multisource_with_translation`` run and the keyword phase of the
    ``full_pipeline`` run executed the same configuration independently.
    Their per-variant disagreement estimates retrieval variance caused by
    live APIs (rate limiting, provider availability, result ordering).
    """
    m3_all = collected["multisource_with_translation"]
    m4_all = collected["full_pipeline"]

    rows: dict[str, Any] = {}
    jaccards: list[float] = []
    recall_diffs: list[float] = []
    n_identical = 0

    for vid in sorted(m3_all):
        gold = variants_by_id[vid].get("relevant_papers") or []
        ids3 = {pid for p in m3_all[vid] if (pid := paper_id(p))}
        m4_keyword, _ = split_full_pipeline(m4_all.get(vid, []))
        ids4 = {pid for p in m4_keyword if (pid := paper_id(p))}

        union = ids3 | ids4
        jac = len(ids3 & ids4) / len(union) if union else 1.0
        r3 = compute_set_recall(m3_all[vid], gold)
        r4 = compute_set_recall(m4_keyword, gold)
        identical = ids3 == ids4

        jaccards.append(jac)
        recall_diffs.append(abs(r4 - r3))
        n_identical += identical
        rows[vid] = {
            "jaccard": jac,
            "identical": identical,
            "set_recall_run1": r3,
            "set_recall_run2": r4,
            "abs_recall_diff": abs(r4 - r3),
        }

    n = len(rows)
    return {
        "description": (
            "Two independent executions of the M3 configuration (the M3 run "
            "and the keyword phase of the M4 run), collected minutes apart "
            "in the same session"
        ),
        "n_variants": n,
        "n_identical_lists": n_identical,
        "mean_jaccard": sum(jaccards) / n if n else 1.0,
        "min_jaccard": min(jaccards) if jaccards else 1.0,
        "mean_abs_set_recall_diff": sum(recall_diffs) / n if n else 0.0,
        "max_abs_set_recall_diff": max(recall_diffs) if recall_diffs else 0.0,
        "per_variant": rows,
    }
