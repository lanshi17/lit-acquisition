"""Analyze 2025-2026 SOTA baselines against the frozen M1-M4 runs.

Computes per-method aggregates and Holm-corrected paired tests for every
SOTA baseline (dense, rerank, hybrid, expand, agent, mdense) against the
PubMed-only baseline (M1) and the strictly nested full pipeline (M4n),
plus per-language gold recovery for the multilingual comparisons.

Outputs ``results/sota_baselines.json``; prints a markdown comparison
table. Frozen M1-M4 numbers are read-only inputs and are never modified.

Usage::

    python benchmarks/sota_analysis.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from analysis.nested import NESTED_METHOD, build_nested_full_pipeline  # noqa: E402
from analysis.stats import holm_correction, paired_test  # noqa: E402
from make_paper_data import load_gold, language_analysis  # noqa: E402
from metrics import (  # noqa: E402
    DEFAULT_KS,
    aggregate_metrics,
    compute_variant_metrics,
)

RESULTS_DIR = BENCH_DIR / "results"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
OUT_FILE = RESULTS_DIR / "sota_baselines.json"

SOTA_METHODS = [
    "sota_dense",
    "sota_rerank",
    "sota_hybrid",
    "sota_expand",
    "sota_agent",
    "sota_mdense",
]

SOTA_LABELS = {
    "sota_dense": "S1 Dense (bge-m3)",
    "sota_rerank": "S2 Cross-encoder rerank",
    "sota_hybrid": "S3 Hybrid BM25+dense+rerank",
    "sota_expand": "S4 LLM query expansion",
    "sota_agent": "S5 Agentic 2-round search",
    "sota_mdense": "S6 Multilingual dense (bge-m3)",
}


def load_collected() -> dict[str, dict[str, list[dict[str, Any]]]]:
    return json.loads(COLLECTED_FILE.read_text())


def series(per_variant: dict[str, Any], metric: str, k: str | None = None) -> list[float]:
    vids = sorted(per_variant)
    if k is None:
        return [getattr(per_variant[v], metric) for v in vids]
    return [getattr(per_variant[v], metric)[k] for v in vids]


def build_tests(
    per_variant: dict[str, dict[str, Any]],
    sota_methods: list[str],
) -> dict[str, dict[str, Any]]:
    """Declared family: SetRecall vs M1 and vs M4n for each SOTA method."""
    tests: dict[str, dict[str, Any]] = {}
    for m in sota_methods:
        for base in ("pubmed_only", NESTED_METHOD):
            name = f"{m}_vs_{base}_set_recall"
            res = paired_test(
                series(per_variant[base], "set_recall"),
                series(per_variant[m], "set_recall"),
            )
            res.update({"base": base, "improved": m, "metric": "set_recall", "k": None})
            tests[name] = res
    holm_correction(tests)
    return tests


def language_recovery(
    methods: dict[str, dict[str, list[dict[str, Any]]]],
    gold: dict[str, dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Per-language gold recovery (found counts) for the given methods."""
    found = language_analysis(methods, gold)["found_by_method"]
    return {
        m: {lang: int(c) for lang, c in langs.items()}
        for m, langs in found.items()
    }


def main() -> None:
    collected = load_collected()
    missing = [m for m in SOTA_METHODS if m not in collected]
    if missing:
        print(f"Missing collected methods (run collect_sota_baselines.py first): {missing}")
        sys.exit(1)

    gold = load_gold()
    nested, _ = build_nested_full_pipeline(collected)

    methods: dict[str, dict[str, list[dict[str, Any]]]] = {
        "pubmed_only": collected["pubmed_only"],
        NESTED_METHOD: nested,
    }
    for m in SOTA_METHODS:
        methods[m] = collected[m]

    # Per-variant metrics + aggregates.
    per_variant: dict[str, dict[str, Any]] = defaultdict(dict)
    all_rows = []
    for method, results in methods.items():
        for vid in sorted(results):
            m = compute_variant_metrics(gold[vid], results[vid], method, DEFAULT_KS)
            all_rows.append(m)
            per_variant[method][vid] = m
    aggregates = aggregate_metrics(all_rows, DEFAULT_KS)

    # Declared test family (12 tests: 6 methods x {M1, M4n}), Holm-corrected.
    tests = build_tests(per_variant, SOTA_METHODS)

    # Per-language recovery: SOTA methods + M1/M3 for reference.
    lang_methods = {
        "pubmed_only": collected["pubmed_only"],
        "multisource_with_translation": collected["multisource_with_translation"],
        **{m: collected[m] for m in SOTA_METHODS},
    }
    lang_rec = language_recovery(lang_methods, gold)

    # Realized candidate counts per method.
    totals = {m: sum(len(v) for v in collected[m].values()) for m in SOTA_METHODS}
    realized = {m: sum(len(v) for v in collected[m].values()) / len(collected[m]) for m in SOTA_METHODS}

    out = {
        "schema_version": 1,
        "collected_at_note": (
            "SOTA baselines collected 2026-08-14; frozen 2026-08-12 M1-M4 "
            "runs unchanged. See results/sota_manifest.json."
        ),
        "methods": {m: SOTA_LABELS[m] for m in SOTA_METHODS},
        "budget": 20,
        "pool": "PubMed esearch retmax=100",
        "aggregates": {m: aggregates[m] for m in ["pubmed_only", NESTED_METHOD, *SOTA_METHODS]},
        "tests_holm": tests,
        "language_recovery": lang_rec,
        "realized_mean_candidates": realized,
        "total_papers": totals,
        "gold": {
            "n_variants": len(gold),
            "n_papers": sum(len(v.get("relevant_papers") or []) for v in gold.values()),
        },
    }
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"sota_baselines.json -> {OUT_FILE}")

    # Markdown table: SetRecall and Recall@20 vs M1/M4n.
    agg = out["aggregates"]
    header = "| Method | SetRecall | Recall@20 | Prec@20 | Prec@Retr | nDCG@20 | Rel. |"
    sep = "| --- | --- | --- | --- | --- | --- | --- |"
    print("\n" + header + "\n" + sep)
    for m in ["pubmed_only", NESTED_METHOD, *SOTA_METHODS]:
        a = agg[m]
        print(
            f"| {SOTA_LABELS.get(m, m)} | {a['set_recall']['mean']:.3f}±{a['set_recall']['std']:.3f} "
            f"| {a['recall_at']['20']['mean']:.3f}±{a['recall_at']['20']['std']:.3f} "
            f"| {a['precision_at']['20']['mean']:.3f}±{a['precision_at']['20']['std']:.3f} "
            f"| {a['precision_at_retrieved']['mean']:.3f}±{a['precision_at_retrieved']['std']:.3f} "
            f"| {a['ndcg_at']['20']['mean']:.3f}±{a['ndcg_at']['20']['std']:.3f} "
            f"| {a['micro']['relevant_found']} |"
        )

    print("\n## Paired tests vs M1 / M4n (SetRecall, Holm-corrected family of 12)")
    print("| Test | Δ | p | p(Holm) | r | 95% CI | sig |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for name, t in tests.items():
        print(
            f"| {name} | {t['delta']:+.3f} | {t['p']:.4f} | {t['p_holm']:.4f} "
            f"| {t['r']:+.2f} | [{t['ci95'][0]:+.3f}, {t['ci95'][1]:+.3f}] "
            f"| {'yes' if t['significant_holm'] else 'no'} |"
        )

    print("\n## Non-English gold recovery (relevant papers found)")
    langs = [lang_name for lang_name in ("de", "fr") if any(lang_name in v for v in lang_rec.values())]
    print("| Method | " + " | ".join(langs) + " |")
    print("| --- | " + " | ".join(["---"] * len(langs)) + " |")
    for m in ["pubmed_only", "multisource_with_translation", *SOTA_METHODS]:
        print("| " + SOTA_LABELS.get(m, m) + " | " + " | ".join(str(lang_rec.get(m, {}).get(lang_name, 0)) for lang_name in langs) + " |")


if __name__ == "__main__":
    main()
