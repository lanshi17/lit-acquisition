"""E4c (part 1): corrected M5 / M5g re-ranking and citation-gating analysis.

Plan reference: ``paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md`` section 4.3 (E4c).

The original ``results/rerank_eval.json`` was produced with a
``rerank_docs`` that restored the provider response to *input* order, so its
``M5`` (full rerank) was identical to ``M4n`` and its ``M5g`` gate kept citation
items purely by position. Both artifacts therefore carry no information about
the cross-encoder.

This script recomputes the same two constructions with the fixed ordering,
entirely from the frozen nested lists:

* ``M5``  --- the whole nested M4n list re-ordered by
  ``BAAI/bge-reranker-v2-m3`` (set unchanged, so SetRecall is identical to M4n);
* ``M5g`` --- the gate proposal of the plan: keep the citation items only when
  their rerank score is at least the minimum score of the top-20 M3 candidates
  ("as relevant as what we already show"), preserving the M3 order.

Declared family: {M5, M5g} x {Recall@20, nDCG@20, Precision@20,
Precision@Retrieved, SetRecall} = 10 paired tests against M4n, Holm-corrected,
on the frozen reference set; the same family is repeated on the pooled
expansion.

Output: ``benchmarks/results/rerank_eval_corrected.json``.

Usage::

    python benchmarks/analysis/rerank_eval_corrected.py [--no-api]
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
from make_paper_data import load_gold  # noqa: E402
from metrics import DEFAULT_KS, aggregate_metrics, compute_variant_metrics  # noqa: E402

RESULTS_DIR = BENCH_DIR / "results"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
OUT_FILE = RESULTS_DIR / "rerank_eval_corrected.json"
GATE_TOP_K = 20


def queries() -> dict[str, str]:
    return {
        json.loads(line)["variant_id"]: json.loads(line)["query"]
        for line in (BENCH_DIR / "gold_standard" / "variants.jsonl").read_text().splitlines()
        if line.strip()
    }


def split(m4: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    keywords = [p for p in m4 if p.get("source") != "citation_graph"]
    citations = [p for p in m4 if p.get("source") == "citation_graph"]
    return keywords, citations


def metric_series(per_variant: dict[str, dict[str, Any]], method: str, metric: str, k: str | None) -> list[float]:
    vids = sorted(per_variant[method])
    if k is None:
        return [getattr(per_variant[method][v], metric) for v in vids]
    return [getattr(per_variant[method][v], metric)[k] for v in vids]


def evaluate(methods: dict[str, dict[str, list[dict[str, Any]]]], gold: dict[str, dict[str, Any]]):
    rows = []
    per_variant: dict[str, dict[str, Any]] = {}
    for method, lists in methods.items():
        per_variant[method] = {}
        for vid in sorted(gold):
            metric = compute_variant_metrics(gold[vid], lists.get(vid) or [], method, DEFAULT_KS)
            rows.append(metric)
            per_variant[method][vid] = metric
    return aggregate_metrics(rows, DEFAULT_KS), per_variant


def run_family(per_variant: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    tests: dict[str, dict[str, Any]] = {}
    for method in ("m5_reranked", "m5g_gated"):
        for metric, k in (("recall_at", "20"), ("ndcg_at", "20"), ("precision_at", "20"),
                          ("precision_at_retrieved", None), ("set_recall", None)):
            name = f"{method}_vs_m4n_{metric}_{k or 'retr'}"
            result = paired_test(
                metric_series(per_variant, "full_pipeline_nested", metric, k),
                metric_series(per_variant, method, metric, k),
            )
            result.update({"base": "full_pipeline_nested", "improved": method, "metric": metric, "k": k})
            tests[name] = result
    holm_correction(tests)
    return tests


async def main_async() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-api", action="store_true")
    args = parser.parse_args()

    collected = json.loads(COLLECTED_FILE.read_text())
    nested, _ = build_nested_full_pipeline(collected)
    gold = load_gold()
    query_by_variant = queries()

    cache = load_cache()
    before = warm_cache_size(cache)
    reranked: dict[str, list[dict[str, Any]]] = {}
    gated: dict[str, list[dict[str, Any]]] = {}
    gate_stats = {"citation_items": 0, "kept_by_gate": 0}
    async with httpx.AsyncClient(timeout=120.0) as client:
        for vid, items in sorted(nested.items()):
            query = query_by_variant[vid]
            scores = await rerank_scores(client, query, items, cache)
            reranked[vid] = order_by_scores(items, scores)

            keywords, citations = split(items)
            scored = list(zip(scores, items))
            scored_m3 = [s for s, item in scored if item in keywords]
            threshold = min(scored_m3[:GATE_TOP_K], default=0.0) if scored_m3 else 0.0
            kept = [item for s, item in scored if item in citations and s >= threshold]
            gate_stats["citation_items"] += len(citations)
            gate_stats["kept_by_gate"] += len(kept)
            gated[vid] = keywords + kept
    if args.no_api and warm_cache_size(cache) != before:
        raise SystemExit("--no-api given but cache misses were issued")
    save_cache(cache)

    methods = {"full_pipeline_nested": nested, "m5_reranked": reranked, "m5g_gated": gated}
    expanded, expansion_report = expanded_gold(gold, {**collected, "full_pipeline_nested": nested})
    frozen_agg, frozen_pv = evaluate(methods, gold)
    expanded_agg, expanded_pv = evaluate(methods, expanded)

    artifact = {
        "schema_version": 1,
        "protocol": {
            "plan_reference": "paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md §4.3 (E4c)",
            "supersedes": "results/rerank_eval.json (produced before the rerank ordering bug was fixed)",
            "base": "frozen nested M4n lists (analysis.nested.build_nested_full_pipeline)",
            "reranker": "BAAI/bge-reranker-v2-m3, English variant query, titles only",
            "gate_rule": (
                f"keep citation items whose rerank score >= the minimum score of the top-{GATE_TOP_K} M3 "
                "candidates; M3 order preserved"
            ),
            "no_live_retrieval": True,
            "declared_family": "M5/M5g x {Recall@20, nDCG@20, Precision@20, Precision@Retrieved, SetRecall} vs M4n",
            "reference_sets": {"frozen": "822 papers", "expanded": f"{total_relevant(expanded)} papers"},
            "cache": warm_cache_size(cache),
            "expansion": {k: expansion_report[k] for k in
                          ("added_total", "frozen_total", "expanded_total", "variants_with_additions")},
        },
        "gate_statistics": gate_stats,
        "aggregates_frozen": frozen_agg,
        "aggregates_expanded": expanded_agg,
        "tests_holm_frozen": run_family(frozen_pv),
        "tests_holm_expanded": run_family(expanded_pv),
    }
    OUT_FILE.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))

    for label, agg in (("FROZEN 822", frozen_agg), ("EXPANDED 1135", expanded_agg)):
        print(f"\n=== {label} ===")
        print("| Method | Recall@20 | nDCG@20 | Prec@20 | Prec@Retr | SetRecall | Cands |")
        print("| --- | --- | --- | --- | --- | --- | --- |")
        for method in ("full_pipeline_nested", "m5_reranked", "m5g_gated"):
            a = agg[method]
            print(f"| {method} | {a['recall_at']['20']['mean']:.3f} | {a['ndcg_at']['20']['mean']:.3f} "
                  f"| {a['precision_at']['20']['mean']:.3f} | {a['precision_at_retrieved']['mean']:.3f} "
                  f"| {a['set_recall']['mean']:.3f} | {a['candidates_per_variant']['mean']:.1f} |")
    print(f"\ngate: kept {gate_stats['kept_by_gate']}/{gate_stats['citation_items']} citation items")
    print("\n| Test | Δ | p | p(Holm) | r | sig |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, t in artifact["tests_holm_frozen"].items():
        print(f"| {name} | {t['delta']:+.4f} | {t['p']:.4f} | {t['p_holm']:.4f} | {t['r']:+.2f} "
              f"| {'yes' if t['significant_holm'] else 'no'} |")
    print(f"\nwrote {OUT_FILE}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
