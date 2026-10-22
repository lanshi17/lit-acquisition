"""E4c (part 2): citation-traversal seed sensitivity (k = 1, 3, 5).

Plan reference: ``paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md`` section 4.3 (E4c).

The recorded pipeline seeds citation traversal with a single DOI (the top-ranked
M3 candidate, ``k=1``, depth 1, budget 20). It recovers two relevant papers that
keyword search did not rank, but it also costs precision. This script measures
what happens when the seed set is widened to the top-3 and top-5 M3 candidates:

* traversal is incremental --- the seeds are traversed once each and the
  ``k=3``/``k=5`` sets are unions of the first 1/3/5 seeds, so the comparison is
  nested by construction;
* results are deduplicated against the frozen M3 list and capped at the same
  20-paper budget as the recorded run;
* metrics are computed on the frozen reference set and on the pooled expansion,
  and the *unique* relevant papers contributed by each seed-set size are listed
  per variant.

Raw Semantic Scholar responses are cached in
``results/citation_seed_cache.json`` so the experiment is re-runnable offline.

Output: ``benchmarks/results/citation_seeds.json``.

Usage::

    python benchmarks/analysis/citation_seeds.py [--no-api] [--budget 20]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from analysis.nested import build_nested_full_pipeline  # noqa: E402
from analysis.pooled_gold import expanded_gold, total_relevant  # noqa: E402
from analysis.stats import holm_correction, paired_test  # noqa: E402
from make_paper_data import load_gold  # noqa: E402
from metrics import DEFAULT_KS, aggregate_metrics, compute_variant_metrics, paper_id  # noqa: E402

RESULTS_DIR = BENCH_DIR / "results"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
CACHE_FILE = RESULTS_DIR / "citation_seed_cache.json"
OUT_FILE = RESULTS_DIR / "citation_seeds.json"
SEED_COUNTS = (1, 3, 5)


def queries() -> dict[str, str]:
    return {
        json.loads(line)["variant_id"]: json.loads(line)["query"]
        for line in (BENCH_DIR / "gold_standard" / "variants.jsonl").read_text().splitlines()
        if line.strip()
    }


def normalize_s2(paper: dict[str, Any], method: str) -> dict[str, Any]:
    """Semantic Scholar paper -> benchmark item (same schema as the pipeline)."""
    from lit_acquisition.normalize import normalize_items

    for item in normalize_items("semantic_scholar", [paper]):
        data = item.model_dump()
        data["method"] = method
        data["source"] = "citation_graph"
        return data
    return {}


def citation_count(paper: dict[str, Any]) -> int:
    return int(paper.get("citationCount") or 0)


async def traverse_all(
    seeds_by_variant: dict[str, list[str]], cache: dict[str, Any], offline: bool
) -> None:
    from lit_acquisition import traverse_citation_graph

    for vid, seeds in seeds_by_variant.items():
        for seed in seeds:
            key = f"{vid}|{seed}"
            if key in cache:
                continue
            if offline:
                cache[key] = []
                continue
            try:
                papers = await traverse_citation_graph(seed=seed, max_depth=1, max_papers=20, direction="both")
                cache[key] = papers
                print(f"  [{vid}] seed {seed}: {len(papers)} related papers")
            except Exception as exc:  # noqa: BLE001
                print(f"  [{vid}] seed {seed} FAILED: {exc}")
                cache[key] = []
            CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))


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


async def main_async() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-api", action="store_true", help="use the cache only")
    parser.add_argument("--budget", type=int, default=20, help="appended citation papers per variant")
    args = parser.parse_args()

    collected = json.loads(COLLECTED_FILE.read_text())
    gold = load_gold()
    query_by_variant = queries()
    nested, _ = build_nested_full_pipeline(collected)
    m3 = collected["multisource_with_translation"]

    # Seeds: the first k M3 candidates that carry a DOI.
    seeds_by_variant: dict[str, list[str]] = {}
    for vid, items in m3.items():
        dois: list[str] = []
        for item in items:
            doi = str(item.get("doi") or "").strip()
            if doi and doi not in dois:
                dois.append(doi)
            if len(dois) >= max(SEED_COUNTS):
                break
        seeds_by_variant[vid] = dois

    cache: dict[str, Any] = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    if not args.no_api:
        await traverse_all(seeds_by_variant, cache, offline=False)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))

    # Build k-nested citation appends.
    appended: dict[int, dict[str, list[dict[str, Any]]]] = {k: {} for k in SEED_COUNTS}
    for vid, items in m3.items():
        seen = {pid for item in items if (pid := paper_id(item))}
        per_seed: list[list[dict[str, Any]]] = []
        for seed in seeds_by_variant[vid][: max(SEED_COUNTS)]:
            papers = cache.get(f"{vid}|{seed}", []) or []
            per_seed.append(sorted(papers, key=citation_count, reverse=True))
        for k in SEED_COUNTS:
            merged: list[dict[str, Any]] = []
            local_seen = set(seen)
            for seed_papers in per_seed[:k]:
                for paper in seed_papers:
                    item = normalize_s2(paper, f"m3_cit_k{k}")
                    pid = paper_id(item)
                    if not pid or pid in local_seen:
                        continue
                    local_seen.add(pid)
                    merged.append(item)
            appended[k][vid] = sorted(merged, key=lambda p: -citation_count(p))[: args.budget]

    methods: dict[str, dict[str, list[dict[str, Any]]]] = {
        "m3_base": m3,
        "m4n_recorded": nested,
        **{f"m3_cit_k{k}": {vid: items + appended[k][vid] for vid, items in m3.items()} for k in SEED_COUNTS},
    }

    expanded, expansion_report = expanded_gold(gold, {**collected, "full_pipeline_nested": nested})
    frozen_agg, frozen_pv = evaluate(methods, gold)
    expanded_agg, expanded_pv = evaluate(methods, expanded)

    # Unique relevant contributions per seed-set size.
    def relevant_found(vid: str, items: list[dict[str, Any]], reference: dict[str, dict[str, Any]]) -> list[str]:
        truth = {pid for p in reference[vid].get("relevant_papers") or [] if (pid := paper_id(p))}
        return [str(p.get("doi") or p.get("pmid") or p.get("title"))[:60]
                for p in items if paper_id(p) in truth]

    contributions = {
        f"k{k}": {
            "papers_appended": sum(len(v) for v in appended[k].values()),
            "relevant_found_frozen": sum(len(relevant_found(vid, appended[k][vid], gold)) for vid in m3),
            "relevant_found_expanded": sum(len(relevant_found(vid, appended[k][vid], expanded)) for vid in m3),
            "relevant_titles": {
                vid: relevant_found(vid, appended[k][vid], expanded)
                for vid in m3 if relevant_found(vid, appended[k][vid], expanded)
            },
        }
        for k in SEED_COUNTS
    }

    tests: dict[str, dict[str, Any]] = {}
    for k in (3, 5):
        for metric, kk in (("recall_at", "20"), ("ndcg_at", "20"), ("precision_at_retrieved", None),
                           ("set_recall", None)):
            name = f"k{k}_vs_k1_{metric}"
            result = paired_test(metric_series(frozen_pv, "m3_cit_k1", metric, kk),
                                 metric_series(frozen_pv, f"m3_cit_k{k}", metric, kk))
            result.update({"base": "m3_cit_k1", "improved": f"m3_cit_k{k}", "metric": metric})
            tests[name] = result
    holm_correction(tests)

    artifact = {
        "schema_version": 1,
        "protocol": {
            "plan_reference": "paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md §4.3 (E4c)",
            "seeds": "the first k M3 candidates carrying a DOI",
            "traversal": "Semantic Scholar, depth 1, direction both, budget 20 per variant",
            "nesting": "k=3 and k=5 extend k=1 by union, so the comparison is nested by construction",
            "budget": args.budget,
            "reference_sets": {"frozen": "822 papers", "expanded": f"{total_relevant(expanded)} papers"},
            "expansion": {k: expansion_report[k] for k in
                          ("added_total", "frozen_total", "expanded_total", "variants_with_additions")},
            "declared_family": [f"k{k}_vs_k1" for k in (3, 5)] + ["metrics: Recall@20, nDCG@20, Prec@Retrieved, SetRecall"],
        },
        "seeds_per_variant": {vid: len(seeds) for vid, seeds in seeds_by_variant.items()},
        "contributions": contributions,
        "aggregates_frozen": frozen_agg,
        "aggregates_expanded": expanded_agg,
        "tests_holm_frozen": tests,
    }
    OUT_FILE.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))

    for label, agg in (("FROZEN 822", frozen_agg), ("EXPANDED 1135", expanded_agg)):
        print(f"\n=== {label} ===")
        print("| Method | Recall@20 | nDCG@20 | Prec@Retr | SetRecall | Cands |")
        print("| --- | --- | --- | --- | --- | --- |")
        for method in ("m3_base", "m3_cit_k1", "m3_cit_k3", "m3_cit_k5", "m4n_recorded"):
            a = agg[method]
            print(f"| {method} | {a['recall_at']['20']['mean']:.3f} | {a['ndcg_at']['20']['mean']:.3f} "
                  f"| {a['precision_at_retrieved']['mean']:.3f} | {a['set_recall']['mean']:.3f} "
                  f"| {a['candidates_per_variant']['mean']:.1f} |")
    print("\n| Seed set | papers appended | relevant (frozen) | relevant (expanded) |")
    print("| --- | --- | --- | --- |")
    for k in SEED_COUNTS:
        c = contributions[f"k{k}"]
        print(f"| k={k} | {c['papers_appended']} | {c['relevant_found_frozen']} "
              f"| {c['relevant_found_expanded']} |")
    print("\n| Test | Δ | p | p(Holm) | r | sig |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, t in tests.items():
        print(f"| {name} | {t['delta']:+.4f} | {t['p']:.4f} | {t['p_holm']:.4f} | {t['r']:+.2f} "
              f"| {'yes' if t['significant_holm'] else 'no'} |")
    print(f"\nwrote {OUT_FILE}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
