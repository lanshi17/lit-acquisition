"""Pooled reference-set expansion artifact (offline; supports E1/E3 and E2a).

The frozen 822-paper reference set was built by scoring a capped 2026-08-14
search pool with the documented heuristic of ``gold_standard/build_variants.py``
(gene +3, variant +5, disease +1, threshold > 4). The ten recorded systems now
provide a much larger pool for the same 43 variants, and re-applying that same
rule surfaces papers the reference set is missing --- including papers whose
titles name the variant explicitly.

This script

1. builds the pooled additions and the expanded reference set
   (``analysis.pooled_gold``),
2. recomputes every recorded method's metrics on both reference sets,
3. re-runs the headline paired tests (vs M1, Holm-corrected) on the expanded
   set,
4. stores a per-variant example list so the additions can be spot-checked.

Output: ``benchmarks/results/expanded_reference.json``.

Usage::

    python benchmarks/analysis/expanded_reference.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from analysis.nested import build_nested_full_pipeline  # noqa: E402
from analysis.pooled_gold import candidate_pool, expanded_gold, total_relevant  # noqa: E402
from analysis.stats import holm_correction, paired_test  # noqa: E402
from make_paper_data import load_gold  # noqa: E402
from metrics import DEFAULT_KS, aggregate_metrics, compute_variant_metrics  # noqa: E402

RESULTS_DIR = BENCH_DIR / "results"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
OUT_FILE = RESULTS_DIR / "expanded_reference.json"

METHOD_LABELS = {
    "pubmed_only": "M1 PubMed only",
    "multisource_no_translation": "M2 multi-source, no translation",
    "multisource_with_translation": "M3 + translation",
    "full_pipeline": "M4 full pipeline (recorded)",
    "full_pipeline_nested": "M4n nested full pipeline",
    "sota_dense": "S1 dense",
    "sota_rerank": "S2 cross-encoder (recorded, unranked pool)",
    "sota_hybrid": "S3 hybrid BM25+dense (recorded, unranked pool)",
    "sota_expand": "S4 LLM query expansion",
    "sota_agent": "S5 agentic two-round",
    "sota_mdense": "S6 multilingual dense",
}

# Declared family on the expanded set: the headline contrasts of the manuscript.
DECLARED = [
    ("m4n_vs_m1_setrecall", "pubmed_only", "full_pipeline_nested", None),
    ("m2_vs_m1_setrecall", "pubmed_only", "multisource_no_translation", None),
    ("m4n_vs_m1_recall20", "pubmed_only", "full_pipeline_nested", "20"),
    ("m4n_vs_m1_ndcg20", "pubmed_only", "full_pipeline_nested", "ndcg"),
    ("m4n_vs_s3_setrecall", "sota_hybrid", "full_pipeline_nested", None),
    ("m4n_vs_s1_setrecall", "sota_dense", "full_pipeline_nested", None),
    ("m4n_vs_s6_setrecall", "sota_mdense", "full_pipeline_nested", None),
    ("m4n_vs_m1_precretrieved", "pubmed_only", "full_pipeline_nested", "retr"),
]


def metric_series(per_variant: dict[str, dict[str, Any]], method: str, kind: str) -> list[float]:
    vids = sorted(per_variant[method])
    if kind is None:
        return [per_variant[method][v].set_recall for v in vids]
    if kind == "retr":
        return [per_variant[method][v].precision_at_retrieved for v in vids]
    if kind == "ndcg":
        return [per_variant[method][v].ndcg_at["20"] for v in vids]
    return [per_variant[method][v].recall_at[kind] for v in vids]


def evaluate(methods: dict[str, dict[str, list[dict[str, Any]]]], gold: dict[str, dict[str, Any]]):
    rows = []
    per_variant: dict[str, dict[str, Any]] = {}
    for method, results in methods.items():
        per_variant[method] = {}
        for vid in sorted(gold):
            metric = compute_variant_metrics(gold[vid], results.get(vid) or [], method, DEFAULT_KS)
            rows.append(metric)
            per_variant[method][vid] = metric
    return aggregate_metrics(rows, DEFAULT_KS), per_variant


def main() -> None:
    collected = json.loads(COLLECTED_FILE.read_text())
    gold = load_gold()
    nested, _ = build_nested_full_pipeline(collected)

    pools: dict[str, dict[str, list[dict[str, Any]]]] = {**collected, "full_pipeline_nested": nested}
    expanded, report = expanded_gold(gold, pools)

    methods: dict[str, dict[str, list[dict[str, Any]]]] = {
        "pubmed_only": collected["pubmed_only"],
        "multisource_no_translation": collected["multisource_no_translation"],
        "multisource_with_translation": collected["multisource_with_translation"],
        "full_pipeline": collected["full_pipeline"],
        "full_pipeline_nested": nested,
        "sota_dense": collected["sota_dense"],
        "sota_rerank": collected["sota_rerank"],
        "sota_hybrid": collected["sota_hybrid"],
        "sota_expand": collected["sota_expand"],
        "sota_agent": collected["sota_agent"],
        "sota_mdense": collected["sota_mdense"],
    }
    frozen_agg, frozen_pv = evaluate(methods, gold)
    expanded_agg, expanded_pv = evaluate(methods, expanded)

    tests: dict[str, dict[str, Any]] = {}
    for name, base, improved, kind in DECLARED:
        result = paired_test(metric_series(expanded_pv, base, kind), metric_series(expanded_pv, improved, kind))
        result.update({"base": base, "improved": improved, "metric": kind})
        tests[name] = result
    holm_correction(tests)

    # Spot-check material: the additions per variant, with the heuristic reason.
    additions_sample = {}
    for vid, papers in (report.get("per_variant") or {}).items():
        additions_sample[vid] = [
            {"title": p.get("title"), "doi": p.get("doi"), "pmid": p.get("pmid"),
             "source": p.get("source"), "reason": p.get("annotation_reason"),
             "score": p.get("pooled_score")}
            for p in (expanded[vid]["relevant_papers"][len(gold[vid]["relevant_papers"]):])
        ]

    pooled_sizes = {
        "pool_size_total": sum(len(candidate_pool(pools, vid)) for vid in gold),
        "frozen_gold_total": total_relevant(gold),
        "expanded_gold_total": total_relevant(expanded),
    }

    artifact = {
        "schema_version": 1,
        "protocol": {
            "plan_reference": (
                "supporting analysis for paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md §1 (E1) and §3 (E3); "
                "extends the reference set the same way §2 (E2) questions it"
            ),
            "no_live_retrieval": True,
            "expansion": report["rule"],
            "caveats": report["caveats"],
        },
        "sizes": {**pooled_sizes, **{k: report[k] for k in
                                     ("frozen_total", "added_total", "expanded_total", "variants_with_additions")}},
        "per_variant_additions": report["per_variant"],
        "additions_sample": additions_sample,
        "method_labels": METHOD_LABELS,
        "aggregates_frozen": frozen_agg,
        "aggregates_expanded": expanded_agg,
        "tests_holm_expanded": tests,
    }
    OUT_FILE.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))

    print(f"pool: {pooled_sizes['pool_size_total']} unique candidate papers across 43 variants")
    print(f"reference set: {pooled_sizes['frozen_gold_total']} -> {pooled_sizes['expanded_gold_total']} "
          f"(+{report['added_total']} on {report['variants_with_additions']} variants)")
    print("\n| Method | SetRecall frozen -> expanded | R@20 frozen -> expanded | nDCG@20 frozen -> expanded |")
    print("| --- | --- | --- | --- |")
    for method in METHOD_LABELS:
        f, e = frozen_agg[method], expanded_agg[method]
        print(f"| {METHOD_LABELS[method]} | {f['set_recall']['mean']:.3f} -> {e['set_recall']['mean']:.3f} "
              f"| {f['recall_at']['20']['mean']:.3f} -> {e['recall_at']['20']['mean']:.3f} "
              f"| {f['ndcg_at']['20']['mean']:.3f} -> {e['ndcg_at']['20']['mean']:.3f} |")
    print("\n| Expanded-set test | Δ | p | p(Holm) | r | sig |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, t in tests.items():
        print(f"| {name} | {t['delta']:+.3f} | {t['p']:.4f} | {t['p_holm']:.4f} | {t['r']:+.2f} "
              f"| {'yes' if t['significant_holm'] else 'no'} |")
    print(f"\nwrote {OUT_FILE}")


if __name__ == "__main__":
    main()
