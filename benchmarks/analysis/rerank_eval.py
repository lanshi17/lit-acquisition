"""Neural-reranking optimization experiment on the frozen nested pipeline.

Motivation (from the 2025-2026 baseline comparison): the cross-encoder
stage is reported as the single largest quality boost in hybrid
pipelines, and the S3 hybrid baseline matched our nDCG@20 (0.311 vs
0.325) despite a much smaller candidate set. We test two offline
optimizations of OUR pipeline, both constructed from frozen lists (no
live re-retrieval, so protocol-clean):

- **M5 (neural rerank)**: the nested M4n candidate list of each variant
  is re-ranked by a cross-encoder (bge-reranker-v2-m3) against the
  variant query. Same set (SetRecall unchanged), better top-K ordering.
- **M5g (gated citation)**: the M3 keyword list keeps its order; only
  citation-graph candidates whose rerank score is at least the minimum
  score of the top-20 M3 candidates ("as relevant as what we already
  show") are appended. Targets the measured precision cost of citation
  traversal (M4n vs M3 Prec@Retr -0.058, p_Holm=0.002).

Declared test family: {M5, M5g} x {Recall@20, nDCG@20, Prec@20,
Prec@Retr, SetRecall} = 10 paired tests vs M4n, Holm-corrected.

Outputs ``results/rerank_eval.json``; prints a comparison table.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from analysis.nested import build_nested_full_pipeline  # noqa: E402
from analysis.stats import holm_correction, paired_test  # noqa: E402
from baselines.sota_common import rerank_docs  # noqa: E402
from make_paper_data import load_gold  # noqa: E402
from metrics import (  # noqa: E402
    DEFAULT_KS,
    aggregate_metrics,
    compute_variant_metrics,
)

RESULTS_DIR = BENCH_DIR / "results"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
OUT_FILE = RESULTS_DIR / "rerank_eval.json"

GATE_TOP_K = 20  # M3 cutoff whose minimum rerank score gates citation items
METHOD = "full_pipeline_nested"
LABELS = {"reranked": "M5 neural rerank", "gated": "M5g gated citation"}


def split_m3_citation(nested_list: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a nested M4n list into (M3 keyword part, citation-graph part)."""
    m3, cg = [], []
    for p in nested_list:
        (cg if p.get("source") == "citation_graph" else m3).append(p)
    return m3, cg


def build_variants(
    collected: dict[str, dict[str, list[dict[str, Any]]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """Nested M4n lists per variant + the variant query per variant."""
    nested, _ = build_nested_full_pipeline(collected)
    queries = {}
    for line in (BENCH_DIR / "gold_standard" / "variants.jsonl").read_text().splitlines():
        if line.strip():
            v = json.loads(line)
            queries[v["variant_id"]] = v["query"]
    return nested, queries


async def compute_scores(
    client: httpx.AsyncClient,
    nested: dict[str, list[dict[str, Any]]],
    queries: dict[str, str],
) -> dict[str, list[tuple[float, dict[str, Any]]]]:
    """Rerank scores for every item of every nested list (score, item)."""
    out: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    for vid, items in sorted(nested.items()):
        scored = await rerank_docs(client, queries[vid], items)
        out[vid] = scored
    return out


def build_optimized(
    nested: dict[str, list[dict[str, Any]]],
    scores: dict[str, list[tuple[float, dict[str, Any]]]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Construct M5 (full rerank) and M5g (gated citation) lists."""
    reranked: dict[str, list[dict[str, Any]]] = {}
    gated: dict[str, list[dict[str, Any]]] = {}
    for vid, items in nested.items():
        # M5: full rerank of the whole list (set unchanged).
        reranked[vid] = [d for _, d in scores[vid]]

        # M5g: keep M3 order; gate citation items by relevance threshold.
        m3, cg = split_m3_citation(items)
        scored_m3 = [s for s in scores[vid] if s[1] in m3]
        threshold = min((s for s, _ in scored_m3[:GATE_TOP_K]), default=0.0)
        kept = [d for s, d in scores[vid] if d in cg and s >= threshold]
        gated[vid] = m3 + kept
    return {"reranked": reranked, "gated": gated}


async def main_async() -> None:
    collected = json.loads(COLLECTED_FILE.read_text())
    gold = load_gold()
    nested, queries = build_variants(collected)

    async with httpx.AsyncClient(timeout=60.0) as client:
        scores = await compute_scores(client, nested, queries)
    optimized = build_optimized(nested, scores)

    # Metrics for M4n (reference), M5, M5g.
    methods: dict[str, dict[str, list[dict[str, Any]]]] = {
        METHOD: nested,
        "reranked": optimized["reranked"],
        "gated": optimized["gated"],
    }
    rows = []
    per_variant: dict[str, dict[str, Any]] = {}
    for method, results in methods.items():
        per_variant[method] = {}
        for vid in sorted(results):
            m = compute_variant_metrics(gold[vid], results[vid], method, DEFAULT_KS)
            rows.append(m)
            per_variant[method][vid] = m
    aggregates = aggregate_metrics(rows, DEFAULT_KS)

    # Paired tests vs M4n: declared family of 10, Holm-corrected.
    def series(method: str, metric: str, k: str | None = None) -> list[float]:
        vids = sorted(per_variant[method])
        if k is None:
            return [getattr(per_variant[method][v], metric) for v in vids]
        return [getattr(per_variant[method][v], metric)[k] for v in vids]

    tests: dict[str, dict[str, Any]] = {}
    for m in ("reranked", "gated"):
        for metric, k in [("recall_at", "20"), ("ndcg_at", "20"), ("precision_at", "20"),
                          ("precision_at_retrieved", None), ("set_recall", None)]:
            name = f"{m}_vs_{METHOD}_{metric}_{k or 'retr'}"
            res = paired_test(series(METHOD, metric, k), series(m, metric, k))
            res.update({"base": METHOD, "improved": m, "metric": metric, "k": k})
            tests[name] = res
    holm_correction(tests)

    out = {
        "schema_version": 1,
        "protocol": {
            "base": "frozen nested M4n lists (2026-08-12 collection)",
            "reranker": "BAAI/bge-reranker-v2-m3 (SiliconFlow)",
            "gate_rule": (
                f"keep citation items with rerank score >= min score of top-{GATE_TOP_K} "
                "M3 candidates; M3 order preserved"
            ),
            "note": "offline construction; no live retrieval; SetRecall of M5 == M4n by construction",
        },
        "aggregates": {m: aggregates[m] for m in (METHOD, "reranked", "gated")},
        "tests_holm": tests,
        "citation_items": {
            "total": sum(1 for items in nested.values() for p in items
                         if p.get("source") == "citation_graph"),
            "kept_by_gate": sum(1 for items in optimized["gated"].values() for p in items
                                if p.get("source") == "citation_graph"),
        },
    }
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"rerank_eval.json -> {OUT_FILE}")

    # Console table.
    print("\n| Method | Recall@20 | Prec@20 | nDCG@20 | Prec@Retr | SetRecall | Rel. | Cands |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for m in (METHOD, "reranked", "gated"):
        a = aggregates[m]
        print(
            f"| {LABELS.get(m, m)} | {a['recall_at']['20']['mean']:.3f}±{a['recall_at']['20']['std']:.3f} "
            f"| {a['precision_at']['20']['mean']:.3f}±{a['precision_at']['20']['std']:.3f} "
            f"| {a['ndcg_at']['20']['mean']:.3f}±{a['ndcg_at']['20']['std']:.3f} "
            f"| {a['precision_at_retrieved']['mean']:.3f}±{a['precision_at_retrieved']['std']:.3f} "
            f"| {a['set_recall']['mean']:.3f}±{a['set_recall']['std']:.3f} "
            f"| {a['micro']['relevant_found']} | {a['candidates_per_variant']['mean']:.1f} |"
        )
    print("\n| Test | Δ | p | p(Holm) | r | sig |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, t in tests.items():
        print(
            f"| {name} | {t['delta']:+.3f} | {t['p']:.4f} | {t['p_holm']:.4f} "
            f"| {t['r']:+.2f} | {'yes' if t['significant_holm'] else 'no'} |"
        )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
