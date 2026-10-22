"""E1: deconfound M2 vs M3 with an orthogonal 2x2 re-ranking design (offline).

Plan reference: ``paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md`` section 1 (E1).

The recorded M2 -> M3 contrast mixes two changes. ``multisource_no_translation``
returns the federated candidate lists in *completion* order --- no
``dedupe_candidates``, no ``rank_candidates``, no cross-encoder --- whereas
``multisource_with_translation`` runs the full
``multilingual_acquisition_workflow`` (dedup + lexical ranking + optional
cross-encoder). The reported ``+0.12`` Recall@20 for M3 therefore cannot be
attributed to translation alone.

This script rebuilds the comparison from the frozen candidate pools only (no
live retrieval) as a symmetric 2x2 factorial. Both pools go through the *same*
code path, so translation is the only difference within a row:

===================  =======================  =========================
                    lexical rank             + cross-encoder rerank
===================  =======================  =========================
no translation       ``M2_lex``                ``M2_rerank``
+ translation        ``M3_lex``                ``M3_rerank``
===================  =======================  =========================

``*_lex`` = ``dedupe_candidates`` + ``rank_candidates(expected_title=query)``,
i.e. exactly what ``multilingual_acquisition_workflow`` does before its optional
cross-encoder stage. ``*_rerank`` adds ``BAAI/bge-reranker-v2-m3`` against the
English query. The recorded orders are kept as ``M2_recorded`` and
``M3_recorded`` so the manuscript's numbers stay traceable. Note that
re-applying the documented ranker to the frozen M3 pool changes Recall@20 by
about +0.03: the recorded permutation is *not* reproducible from the stored
list, which is reported as an artifact caveat rather than silently fixed.

Every method is measured on both reference sets:

* the frozen 822-paper set (the manuscript's numbers), and
* the pooled expansion (``analysis.pooled_gold``: the project's own relevance
  rule applied to the union of every system's candidates).

Declared test family (10 paired Wilcoxon tests per reference set,
Holm-corrected within each set):

1.  ``M2_recorded -> M2_lex``                       Recall@20
2.  ``M2_lex -> M2_rerank``                        Recall@20
3.  ``M2_lex -> M3_lex``                           Recall@20  (translation, lexical rung)
4.  ``M2_rerank -> M3_rerank``                     Recall@20  (translation, reranked rung)
5.  ``M3_lex -> M3_rerank``                        Recall@20
6.  ``M1 -> M2_rerank``                            SetRecall
7.  ``M1 -> M2_rerank``                            Recall@20
8.  ``M2_lex -> M3_lex``                           SetRecall
9.  ``M2_rerank -> M3_rerank``                     SetRecall
10. ``M1 -> M2_recorded``                          SetRecall  (headline multi-source gain)

A difference-in-differences estimate of the interaction is reported alongside
the two translation contrasts.

Output: ``benchmarks/results/m2_deconfounded.json``.

Usage::

    python benchmarks/analysis/deconfound_m2.py            # uses the score cache
    python benchmarks/analysis/deconfound_m2.py --no-api   # cache only, no requests
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from analysis.api_scores import load_cache, order_by_scores, rerank_scores, save_cache, warm_cache_size  # noqa: E402
from analysis.nested import build_nested_full_pipeline  # noqa: E402
from analysis.pooled_gold import expanded_gold, total_relevant  # noqa: E402
from analysis.stats import holm_correction, paired_test  # noqa: E402
from lit_acquisition.algorithms import dedupe_candidates, rank_candidates  # noqa: E402
from make_paper_data import load_gold  # noqa: E402
from metrics import DEFAULT_KS, aggregate_metrics, compute_variant_metrics, paper_id  # noqa: E402

RESULTS_DIR = BENCH_DIR / "results"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
OUT_FILE = RESULTS_DIR / "m2_deconfounded.json"

METHODS = {
    "pubmed_only": "M1 PubMed only (recorded)",
    "m2_recorded": "M2 recorded federation order",
    "m2_lex": "M2_lex dedup + lexical rank",
    "m2_rerank": "M2_rerank + cross-encoder",
    "m3_recorded": "M3 recorded order (manuscript)",
    "m3_lex": "M3_lex dedup + lexical rank",
    "m3_rerank": "M3_rerank + cross-encoder",
    "full_pipeline_nested": "M4n nested full pipeline",
}

DECLARED: list[tuple[str, str, str, str | None]] = [
    ("ranking_on_multisource_recall20", "m2_recorded", "m2_lex", "20"),
    ("crossencoder_on_multisource_recall20", "m2_lex", "m2_rerank", "20"),
    ("translation_lexical_rung_recall20", "m2_lex", "m3_lex", "20"),
    ("translation_reranked_rung_recall20", "m2_rerank", "m3_rerank", "20"),
    ("crossencoder_on_translated_recall20", "m3_lex", "m3_rerank", "20"),
    ("multisource_vs_m1_setrecall", "pubmed_only", "m2_rerank", None),
    ("multisource_vs_m1_recall20", "pubmed_only", "m2_rerank", "20"),
    ("translation_lexical_rung_setrecall", "m2_lex", "m3_lex", None),
    ("translation_reranked_rung_setrecall", "m2_rerank", "m3_rerank", None),
    ("raw_federation_vs_m1_setrecall", "pubmed_only", "m2_recorded", None),
]


def queries() -> dict[str, str]:
    out = {}
    for line in (BENCH_DIR / "gold_standard" / "variants.jsonl").read_text().splitlines():
        if line.strip():
            variant = json.loads(line)
            out[variant["variant_id"]] = variant["query"]
    return out


def build_lexical(
    pool: dict[str, list[dict[str, Any]]], query_by_variant: dict[str, str]
) -> dict[str, list[dict[str, Any]]]:
    """Dedup + lexical rank exactly as ``multilingual_acquisition_workflow`` does."""
    out = {}
    for vid, items in pool.items():
        deduped = dedupe_candidates(items)
        kept = [c for c in deduped if (str(c.get("title") or "").strip() or c.get("doi") or c.get("url"))]
        out[vid] = rank_candidates(kept, expected_title=query_by_variant[vid])
    return out


async def add_rerank(
    client: httpx.AsyncClient,
    lists: dict[str, list[dict[str, Any]]],
    query_by_variant: dict[str, str],
    cache: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Cross-encoder rerank of each variant's list against the English query."""
    out = {}
    for vid, items in sorted(lists.items()):
        scores = await rerank_scores(client, query_by_variant[vid], items, cache)
        out[vid] = order_by_scores(items, scores)
    return out


def per_language_found(
    results: dict[str, list[dict[str, Any]]],
    gold: dict[str, dict[str, Any]],
    languages: tuple[str, ...] = ("de", "fr", "en"),
) -> dict[str, Any]:
    """Gold (variant, paper) pairs recovered per gold language label."""
    totals: dict[str, set[tuple[str, str]]] = {lang: set() for lang in languages}
    for vid, variant in gold.items():
        for paper in variant.get("relevant_papers") or []:
            lang = paper.get("language") or "unknown"
            pid = paper_id(paper)
            if pid and lang in totals:
                totals[lang].add((vid, pid))
    out: dict[str, Any] = {}
    for method, lists in results.items():
        found: set[tuple[str, str]] = set()
        for vid, papers in lists.items():
            for paper in papers:
                pid = paper_id(paper)
                if pid:
                    found.add((vid, pid))
        out[method] = {lang: len(totals[lang] & found) for lang in languages}
        out[method]["_totals"] = {lang: len(totals[lang]) for lang in languages}
    return out


def metric_series(per_variant: dict[str, dict[str, Any]], method: str, kind: str | None) -> list[float]:
    vids = sorted(per_variant[method])
    if kind is None:
        return [per_variant[method][v].set_recall for v in vids]
    if kind == "retr":
        return [per_variant[method][v].precision_at_retrieved for v in vids]
    if kind == "ndcg":
        return [per_variant[method][v].ndcg_at["20"] for v in vids]
    return [per_variant[method][v].recall_at[kind] for v in vids]


def evaluate(
    methods_results: dict[str, dict[str, list[dict[str, Any]]]],
    gold: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    rows = []
    per_variant: dict[str, dict[str, Any]] = {}
    for method, results in methods_results.items():
        per_variant[method] = {}
        for vid in sorted(gold):
            metric = compute_variant_metrics(gold[vid], results.get(vid) or [], method, DEFAULT_KS)
            rows.append(metric)
            per_variant[method][vid] = metric
    return aggregate_metrics(rows, DEFAULT_KS), per_variant


def run_family(per_variant: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    tests: dict[str, dict[str, Any]] = {}
    for name, base, improved, kind in DECLARED:
        result = paired_test(metric_series(per_variant, base, kind), metric_series(per_variant, improved, kind))
        result.update({"base": base, "improved": improved, "metric": kind})
        tests[name] = result
    holm_correction(tests)
    return tests


def interaction(per_variant: dict[str, dict[str, Any]]) -> dict[str, Any]:
    def mean_recall20(method: str) -> float:
        values = metric_series(per_variant, method, "20")
        return sum(values) / len(values)

    cells = {m: mean_recall20(m) for m in ("m2_lex", "m2_rerank", "m3_lex", "m3_rerank")}
    return {
        "estimate": (cells["m3_rerank"] - cells["m2_rerank"]) - (cells["m3_lex"] - cells["m2_lex"]),
        "cells_recall20": cells,
        "note": (
            "Difference-in-differences of Recall@20: (M3r - M2r) - (M3l - M2l). Positive means the "
            "cross-encoder adds more on the translated pool."
        ),
    }


async def main_async() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-api", action="store_true", help="fail instead of issuing cache misses")
    args = parser.parse_args()

    collected = json.loads(COLLECTED_FILE.read_text())
    gold = load_gold()
    query_by_variant = queries()
    nested, _ = build_nested_full_pipeline(collected)

    m2_recorded = collected["multisource_no_translation"]
    m3_recorded = collected["multisource_with_translation"]
    m2_lex = build_lexical(m2_recorded, query_by_variant)
    m3_lex = build_lexical(m3_recorded, query_by_variant)

    cache = load_cache()
    before = warm_cache_size(cache)
    async with httpx.AsyncClient(timeout=90.0) as client:
        m2_rerank = await add_rerank(client, m2_lex, query_by_variant, cache)
        m3_rerank = await add_rerank(client, m3_lex, query_by_variant, cache)
    if args.no_api and warm_cache_size(cache) != before:
        raise SystemExit("--no-api given but cache misses were issued")
    save_cache(cache)

    methods_results: dict[str, dict[str, list[dict[str, Any]]]] = {
        "pubmed_only": collected["pubmed_only"],
        "m2_recorded": m2_recorded,
        "m2_lex": m2_lex,
        "m2_rerank": m2_rerank,
        "m3_recorded": m3_recorded,
        "m3_lex": m3_lex,
        "m3_rerank": m3_rerank,
        "full_pipeline_nested": nested,
    }

    expanded, expansion_report = expanded_gold(gold, {**collected, "full_pipeline_nested": nested})
    frozen_agg, frozen_pv = evaluate(methods_results, gold)
    expanded_agg, expanded_pv = evaluate(methods_results, expanded)

    candidate_counts = {
        method: {
            "mean": sum(len(v) for v in results.values()) / len(results),
            "total": sum(len(v) for v in results.values()),
        }
        for method, results in methods_results.items()
    }
    dedupe_report = {
        "m2_recorded_items": candidate_counts["m2_recorded"]["total"],
        "m2_lex_items": candidate_counts["m2_lex"]["total"],
        "duplicates_removed": candidate_counts["m2_recorded"]["total"] - candidate_counts["m2_lex"]["total"],
    }

    artifact = {
        "schema_version": 1,
        "protocol": {
            "plan_reference": "paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md §1 (E1)",
            "design": "symmetric 2x2 factorial (translation x ranking) built offline from frozen candidate pools",
            "no_live_retrieval": True,
            "pools": {
                "M1": "frozen collected_results.json['pubmed_only']",
                "M2_recorded": "frozen collected_results.json['multisource_no_translation'] (completion order)",
                "M3_recorded": "frozen collected_results.json['multisource_with_translation'] (manuscript order)",
                "M4n": "nested full pipeline (analysis.nested.build_nested_full_pipeline)",
            },
            "lexical_construction": (
                "dedupe_candidates() then rank_candidates(expected_title=query) with empty candidates dropped, "
                "applied identically to the frozen M2 and M3 pools"
            ),
            "reranker": "BAAI/bge-reranker-v2-m3, query = the English variant query (same as the pipeline)",
            "reference_sets": {
                "frozen": "822 papers (manuscript reference set)",
                "expanded": f"{total_relevant(expanded)} papers (pooled expansion, analysis.pooled_gold)",
            },
            "declared_family": [name for name, *_ in DECLARED],
            "cache": warm_cache_size(cache),
        },
        "dedupe_report": dedupe_report,
        "candidate_counts": candidate_counts,
        "method_labels": METHODS,
        "aggregates_frozen": frozen_agg,
        "aggregates_expanded": expanded_agg,
        "tests_holm_frozen": run_family(frozen_pv),
        "tests_holm_expanded": run_family(expanded_pv),
        "interaction_recall20_frozen": interaction(frozen_pv),
        "interaction_recall20_expanded": interaction(expanded_pv),
        "language_recovery_frozen": per_language_found(methods_results, gold),
        "language_recovery_expanded": per_language_found(methods_results, expanded),
        "expansion": {k: expansion_report[k] for k in
                      ("added_total", "variants_with_additions", "frozen_total", "expanded_total")},
        "caveats": (
            "The recorded M3 permutation is not reproducible by re-applying the documented ranker to the "
            "stored M3 list (Recall@20 0.276 recorded vs about 0.306 re-ranked); both are reported. The "
            "expanded reference set is heuristic and title-based, like the frozen one."
        ),
    }
    OUT_FILE.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))

    # ── console ───────────────────────────────────────────────────────────
    print(f"cache: {warm_cache_size(cache)}   dedupe: {dedupe_report}")
    for label, agg in (("FROZEN 822", frozen_agg), ("EXPANDED 1135", expanded_agg)):
        print(f"\n=== {label} ===")
        print("| Method | Recall@20 | nDCG@20 | Prec@Retr | SetRecall | Cands |")
        print("| --- | --- | --- | --- | --- | --- |")
        for method in METHODS:
            a = agg[method]
            print(f"| {METHODS[method]} | {a['recall_at']['20']['mean']:.3f}±{a['recall_at']['20']['std']:.3f} "
                  f"| {a['ndcg_at']['20']['mean']:.3f}±{a['ndcg_at']['20']['std']:.3f} "
                  f"| {a['precision_at_retrieved']['mean']:.3f} | {a['set_recall']['mean']:.3f} "
                  f"| {candidate_counts[method]['mean']:.1f} |")
    for label, tests in (("FROZEN", artifact["tests_holm_frozen"]),
                         ("EXPANDED", artifact["tests_holm_expanded"])):
        print(f"\n=== declared family ({label}) ===")
        print("| Test | Δ | p | p(Holm) | r | sig |")
        print("| --- | --- | --- | --- | --- | --- |")
        for name, t in tests.items():
            print(f"| {name} | {t['delta']:+.3f} | {t['p']:.4f} | {t['p_holm']:.4f} | {t['r']:+.2f} "
                  f"| {'yes' if t['significant_holm'] else 'no'} |")
    print(f"\ninteraction Recall@20 (frozen): {artifact['interaction_recall20_frozen']['estimate']:+.3f}"
          f"  cells={artifact['interaction_recall20_frozen']['cells_recall20']}")
    print(f"interaction Recall@20 (expanded): {artifact['interaction_recall20_expanded']['estimate']:+.3f}")
    print("\n| Method | de | fr | en |")
    print("| --- | --- | --- | --- |")
    for method in METHODS:
        row = artifact["language_recovery_frozen"][method]
        print(f"| {METHODS[method]} | {row['de']}/{row['_totals']['de']} | {row['fr']}/{row['_totals']['fr']} "
              f"| {row['en']}/{row['_totals']['en']} |")
    print(f"\nwrote {OUT_FILE}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
