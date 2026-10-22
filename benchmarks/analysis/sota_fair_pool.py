"""E3: dual-track SOTA evaluation on unbound candidate pools (offline).

Plan reference: ``paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md`` section 3 (E3).

Four of the six recorded SOTA baselines (S1 dense, S2 cross-encoder, S5 agentic,
S6 multilingual dense) take the PubMed pool as their input, so their *set*
recall is identical to M1's by construction and comparing them with the
19-channel pipeline on SetRecall is unfair. At the same time the recorded S2 and
S3 runs are uninformative about ranking: ``baselines.sota_common.rerank_docs``
restored the provider response to input order, so their cross-encoder stage was
a no-op (fixed in that module; see ``tests/test_rerank_ordering.py``).

This script therefore rebuilds the comparison offline on **frozen** pools, with
every ranker applied by the same cached code path:

**Track 1 --- fixed PubMed pool** (the frozen M1 lists): the ranking-only
contest. Set recall is constant across the track by construction; Recall@20 and
nDCG@20 measure how well each ranker orders the *same* candidates.

**Track 2 --- fixed multi-source pool** (the frozen M2 lists): the same rankers
on the federated pool, which is where the pipeline's own lexical/neural rankers
operate.

Rankers (identical implementations in both tracks):
``recorded`` (pool order), ``lexical`` (pipeline dedup + ``rank_candidates``),
``dense`` (S1: bge-m3 cosine with the English query), ``rerank`` (S2:
bge-reranker-v2-m3 over titles), ``hybrid_rrf`` (S3: RRF of pool order and dense
order, then cross-encoder), ``mdense`` (S6: max cosine over the recorded
six-language translations).

Metrics are computed on the frozen reference set and on the pooled expansion
(``analysis.pooled_gold``); paired Wilcoxon tests are Holm-corrected within a
declared family per track.

Output: ``benchmarks/results/sota_fair_pool.json``.

Usage::

    python benchmarks/analysis/sota_fair_pool.py            # uses the caches
    python benchmarks/analysis/sota_fair_pool.py --no-api   # cache only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from analysis.api_scores import (  # noqa: E402
    cosine,
    embed_texts,
    load_cache,
    order_by_scores,
    rerank_scores,
    save_cache,
    warm_cache_size,
)
from analysis.nested import build_nested_full_pipeline  # noqa: E402
from analysis.pooled_gold import expanded_gold, total_relevant  # noqa: E402
from analysis.stats import holm_correction, paired_test  # noqa: E402
from lit_acquisition.algorithms import dedupe_candidates, rank_candidates  # noqa: E402
from make_paper_data import load_gold  # noqa: E402
from metrics import DEFAULT_KS, aggregate_metrics, compute_variant_metrics  # noqa: E402

RESULTS_DIR = BENCH_DIR / "results"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
TRANSLATIONS_FILE = RESULTS_DIR / "translated_queries.json"
OUT_FILE = RESULTS_DIR / "sota_fair_pool.json"

RRF_K = 60
TRACK1_METHODS = ["t1_recorded", "t1_lexical", "t1_dense", "t1_rerank", "t1_hybrid_rrf", "t1_mdense"]
TRACK2_METHODS = ["m2_recorded", "m2_lex", "m2_rerank", "t2_dense", "t2_rerank", "t2_hybrid_rrf", "t2_mdense",
                  "m3_recorded", "full_pipeline_nested"]
LABELS = {
    "t1_recorded": "T1 recorded PubMed order (=M1)",
    "t1_lexical": "T1 + pipeline lexical rank",
    "t1_dense": "T1 + bge-m3 dense (S1 recipe)",
    "t1_rerank": "T1 + cross-encoder (S2 recipe, corrected)",
    "t1_hybrid_rrf": "T1 + RRF(dense, order) (S3 fusion stage)",
    "t1_mdense": "T1 + multilingual dense (S6 recipe)",
    "m2_recorded": "T2 recorded federation order (=M2)",
    "m2_lex": "T2 + pipeline lexical rank",
    "m2_rerank": "T2 + cross-encoder",
    "t2_dense": "T2 + bge-m3 dense (S1 recipe)",
    "t2_rerank": "T2 + cross-encoder (S2 recipe, corrected)",
    "t2_hybrid_rrf": "T2 + RRF(dense, order) (S3 fusion stage)",
    "t2_mdense": "T2 + multilingual dense (S6 recipe)",
    "m3_recorded": "M3 recorded (federated + translation)",
    "full_pipeline_nested": "M4n nested full pipeline",
}

# Declared family: within-track ranker comparisons (12 tests), Holm-corrected.
DECLARED = [
    ("t1_lexical_vs_recorded", "t1_recorded", "t1_lexical"),
    ("t1_dense_vs_recorded", "t1_recorded", "t1_dense"),
    ("t1_rerank_vs_recorded", "t1_recorded", "t1_rerank"),
    ("t1_hybrid_vs_recorded", "t1_recorded", "t1_hybrid_rrf"),
    ("t1_mdense_vs_recorded", "t1_recorded", "t1_mdense"),
    ("t1_hybrid_vs_dense", "t1_dense", "t1_hybrid_rrf"),
    ("t2_lexical_vs_recorded", "m2_recorded", "m2_lex"),
    ("t2_dense_vs_recorded", "m2_recorded", "t2_dense"),
    ("t2_rerank_vs_recorded", "m2_recorded", "t2_rerank"),
    ("t2_hybrid_vs_recorded", "m2_recorded", "t2_hybrid_rrf"),
    ("t2_mdense_vs_recorded", "m2_recorded", "t2_mdense"),
    ("t2_lexical_vs_dense", "t2_dense", "m2_lex"),
]


def queries_and_translations() -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    queries = {}
    for line in (BENCH_DIR / "gold_standard" / "variants.jsonl").read_text().splitlines():
        if line.strip():
            variant = json.loads(line)
            queries[variant["variant_id"]] = variant["query"]
    translations = json.loads(TRANSLATIONS_FILE.read_text())
    return queries, translations


def lexical_order(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    kept = [c for c in dedupe_candidates(items)
            if (str(c.get("title") or "").strip() or c.get("doi") or c.get("url"))]
    return rank_candidates(kept, expected_title=query)


def rrf_order(pool: list[dict[str, Any]], dense_order: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reciprocal rank fusion of the pool order and the dense order."""
    position = {id(item): i for i, item in enumerate(pool)}
    scores: dict[int, float] = defaultdict(float)
    for rank, item in enumerate(pool, start=1):
        scores[position[id(item)]] += 1.0 / (RRF_K + rank)
    for rank, item in enumerate(dense_order, start=1):
        scores[position[id(item)]] += 1.0 / (RRF_K + rank)
    return [pool[i] for i in sorted(scores, key=lambda i: scores[i], reverse=True)]


async def dense_order(
    client: httpx.AsyncClient, query: str, pool: list[dict[str, Any]], cache: dict[str, Any]
) -> list[dict[str, Any]]:
    vectors = await embed_texts(client, [query, *[str(p.get("title") or "") for p in pool]], cache)
    query_vec, doc_vecs = vectors[0], vectors[1:]
    return order_by_scores(pool, [cosine(query_vec, v) for v in doc_vecs])


async def mdense_order(
    client: httpx.AsyncClient,
    query: str,
    translations: dict[str, str],
    pool: list[dict[str, Any]],
    cache: dict[str, Any],
) -> list[dict[str, Any]]:
    lang_queries = [q for q in translations.values() if str(q).strip()] or [query]
    query_vecs = await embed_texts(client, lang_queries, cache)
    doc_vecs = await embed_texts(client, [str(p.get("title") or "") for p in pool], cache)
    scores = [max((cosine(qv, dv) for qv in query_vecs), default=0.0) for dv in doc_vecs]
    return order_by_scores(pool, scores)


def metric_series(per_variant: dict[str, dict[str, Any]], method: str, kind: str) -> list[float]:
    vids = sorted(per_variant[method])
    if kind == "set":
        return [per_variant[method][v].set_recall for v in vids]
    if kind == "ndcg":
        return [per_variant[method][v].ndcg_at["20"] for v in vids]
    return [per_variant[method][v].recall_at[kind] for v in vids]


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


async def main_async() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-api", action="store_true", help="fail instead of issuing cache misses")
    args = parser.parse_args()

    collected = json.loads(COLLECTED_FILE.read_text())
    gold = load_gold()
    nested, _ = build_nested_full_pipeline(collected)
    queries, translations = queries_and_translations()

    pool_pubmed = collected["pubmed_only"]
    pool_multi = collected["multisource_no_translation"]

    cache = load_cache()
    before = warm_cache_size(cache)
    methods: dict[str, dict[str, list[dict[str, Any]]]] = {
        "t1_recorded": pool_pubmed,
        "m2_recorded": pool_multi,
        "m3_recorded": collected["multisource_with_translation"],
        "full_pipeline_nested": nested,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        for track, pool in (("t1", pool_pubmed), ("t2", pool_multi)):
            prefix = "" if track == "t1" else "t2_"
            dense_lists: dict[str, list[dict[str, Any]]] = {}
            mdense_lists: dict[str, list[dict[str, Any]]] = {}
            rerank_lists: dict[str, list[dict[str, Any]]] = {}
            hybrid_lists: dict[str, list[dict[str, Any]]] = {}
            for vid, items in sorted(pool.items()):
                query = queries[vid]
                dense_lists[vid] = await dense_order(client, query, items, cache)
                mdense_lists[vid] = await mdense_order(client, query, translations.get(vid, {}), items, cache)
                scores = await rerank_scores(client, query, items, cache)
                rerank_lists[vid] = order_by_scores(items, scores)
                # S3's fusion stage on its own: the cross-encoder that S3 adds
                # afterwards is identical to the ``rerank`` ranker, so fusing
                # and reranking here would just duplicate it.
                hybrid_lists[vid] = rrf_order(items, dense_lists[vid])
            if track == "t1":
                methods["t1_lexical"] = {vid: lexical_order(items, queries[vid]) for vid, items in pool.items()}
                methods["t1_dense"] = dense_lists
                methods["t1_mdense"] = mdense_lists
                methods["t1_rerank"] = rerank_lists
                methods["t1_hybrid_rrf"] = hybrid_lists
            else:
                m2_lex = {vid: lexical_order(items, queries[vid]) for vid, items in pool.items()}
                methods["m2_lex"] = m2_lex
                methods["t2_dense"] = dense_lists
                methods["t2_mdense"] = mdense_lists
                methods["t2_rerank"] = rerank_lists
                methods["t2_hybrid_rrf"] = hybrid_lists
                # The pipeline's own reranked variant (E1's M2_rerank): cross-encoder
                # on the lexically ranked multi-source pool.
                m2_rerank: dict[str, list[dict[str, Any]]] = {}
                for vid, items in sorted(m2_lex.items()):
                    scores = await rerank_scores(client, queries[vid], items, cache)
                    m2_rerank[vid] = order_by_scores(items, scores)
                methods["m2_rerank"] = m2_rerank
    if args.no_api and warm_cache_size(cache) != before:
        raise SystemExit("--no-api given but cache misses were issued")
    save_cache(cache)

    expanded, expansion_report = expanded_gold(gold, {**collected, "full_pipeline_nested": nested})
    frozen_agg, frozen_pv = evaluate(methods, gold)
    expanded_agg, expanded_pv = evaluate(methods, expanded)

    tests: dict[str, dict[str, Any]] = {}
    for name, base, improved in DECLARED:
        result = paired_test(metric_series(frozen_pv, base, "20"), metric_series(frozen_pv, improved, "20"))
        result.update({"base": base, "improved": improved, "metric": "recall_at_20"})
        tests[name] = result
    holm_correction(tests)
    expanded_tests: dict[str, dict[str, Any]] = {}
    for name, base, improved in DECLARED:
        result = paired_test(
            metric_series(expanded_pv, base, "20"), metric_series(expanded_pv, improved, "20")
        )
        result.update({"base": base, "improved": improved, "metric": "recall_at_20"})
        expanded_tests[name] = result
    holm_correction(expanded_tests)

    artifact = {
        "schema_version": 1,
        "protocol": {
            "plan_reference": "paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md §3 (E3)",
            "design": "dual-track evaluation on frozen pools; every ranker applied by the same cached code path",
            "no_live_retrieval": True,
            "track1_pool": "frozen collected_results.json['pubmed_only'] (the M1 pool)",
            "track2_pool": "frozen collected_results.json['multisource_no_translation'] (the M2 pool)",
            "rankers": {
                "lexical": "dedupe_candidates + rank_candidates (the pipeline's own ordering)",
                "dense": "bge-m3 cosine, English query (S1 recipe)",
                "rerank": "bge-reranker-v2-m3 over titles (S2 recipe, corrected ordering)",
                "hybrid_rrf": (
                    f"RRF(k={RRF_K}) of pool order and dense order --- the S3 fusion stage on its own; "
                    "S3's trailing cross-encoder is measured by the ``rerank`` ranker"
                ),
                "mdense": "max bge-m3 cosine over the recorded six-language translations (S6 recipe)",
            },
            "set_recall_note": (
                "Within a track every method sees the same candidate set, so SetRecall is constant and only "
                "the ranking metrics differ; SetRecall differences across tracks are the federation effect."
            ),
            "reference_sets": {
                "frozen": "822 papers (manuscript reference set)",
                "expanded": f"{total_relevant(expanded)} papers (pooled expansion)",
            },
            "declared_family": [name for name, *_ in DECLARED],
            "cache": warm_cache_size(cache),
            "expansion": {k: expansion_report[k] for k in
                          ("added_total", "frozen_total", "expanded_total", "variants_with_additions")},
        },
        "method_labels": LABELS,
        "aggregates_frozen": frozen_agg,
        "aggregates_expanded": expanded_agg,
        "tests_holm_frozen": tests,
        "tests_holm_expanded": expanded_tests,
    }
    OUT_FILE.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))

    for label, agg in (("FROZEN 822", frozen_agg), ("EXPANDED 1135", expanded_agg)):
        print(f"\n=== {label} ===")
        print("| Method | Recall@20 | nDCG@20 | Prec@20 | Prec@Retr | SetRecall | Cands |")
        print("| --- | --- | --- | --- | --- | --- | --- |")
        for method in [*TRACK1_METHODS, *TRACK2_METHODS]:
            a = agg[method]
            print(f"| {LABELS[method]} | {a['recall_at']['20']['mean']:.3f} "
                  f"| {a['ndcg_at']['20']['mean']:.3f} | {a['precision_at']['20']['mean']:.3f} "
                  f"| {a['precision_at_retrieved']['mean']:.3f} | {a['set_recall']['mean']:.3f} "
                  f"| {a['candidates_per_variant']['mean']:.1f} |")
    print("\n=== declared family (frozen) ===")
    print("| Test | Δ Recall@20 | p | p(Holm) | r | sig |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, t in tests.items():
        print(f"| {name} | {t['delta']:+.4f} | {t['p']:.4f} | {t['p_holm']:.4f} | {t['r']:+.2f} "
              f"| {'yes' if t['significant_holm'] else 'no'} |")
    print(f"\nwrote {OUT_FILE}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
