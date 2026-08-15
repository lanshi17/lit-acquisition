"""Deterministically regenerate every number used by the paper.

Single source of truth for ``results/paper_data.json``: loads the raw
collected candidate lists + gold standard, derives the strictly nested M4
offline, computes corrected metrics (fixed-denominator Precision@K,
Precision@Retrieved, SetRecall, multi-K, macro+micro), runs the paired
Wilcoxon family with Holm correction and bootstrap CIs, quantifies
run-to-run API variance, isolates the translation contribution per
language, and parses the collection log for provider-failure statistics
and the run manifest.

Usage:
    python benchmarks/make_paper_data.py

Outputs (benchmarks/results/):
    derived_results.json   nested-M4 candidate lists (protocol-correct ablation)
    metrics.json           per-variant per-method metric rows (new schema)
    paper_data.json        aggregates, statistics, analyses, run manifest
    summary.md             human-readable summary tables
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).parent
RESULTS_DIR = BENCH_DIR / "results"
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from analysis.nested import (  # noqa: E402
    NESTED_METHOD,
    build_nested_full_pipeline,
    run_variance_analysis,
    split_full_pipeline,
    verify_monotonicity,
)
from analysis.stats import BOOT_N, BOOT_SEED, holm_correction, paired_test  # noqa: E402
from metrics import (  # noqa: E402
    DEFAULT_KS,
    aggregate_metrics,
    compute_variant_metrics,
    format_summary_table,
    paper_id,
)

COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
LOG_FILE = RESULTS_DIR / "collect_20260812.log"
TIMING_FILE = RESULTS_DIR / "timing.json"
EXTERNAL_FILE = RESULTS_DIR / "external_baselines.json"
GOLD_FILE = BENCH_DIR / "gold_standard" / "variants.jsonl"

METHOD_ORDER = [
    "pubmed_only",
    "multisource_no_translation",
    "multisource_with_translation",
    NESTED_METHOD,
]
METHOD_LABELS = {
    "pubmed_only": "M1 PubMed-only",
    "multisource_no_translation": "M2 Multi-source",
    "multisource_with_translation": "M3 +Translation",
    NESTED_METHOD: "M4 +Citation graph (nested)",
    "full_pipeline": "M4' independent re-run (non-nested)",
}


def load_gold() -> dict[str, dict[str, Any]]:
    variants: dict[str, dict[str, Any]] = {}
    with open(GOLD_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                v = json.loads(line)
                variants[v["variant_id"]] = v
    return variants


# ── Per-language analysis (isolates the translation component) ────────────


def language_analysis(
    methods_results: dict[str, dict[str, list[dict[str, Any]]]],
    gold: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Per-language gold recovery per method.

    The M2-vs-M3 contrast is the controlled comparison for the translation
    component: identical providers and budgets, with LLM query translation
    as the only difference.
    """
    gold_by_lang: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for vid, v in gold.items():
        for p in v.get("relevant_papers") or []:
            pid = paper_id(p)
            if pid:
                gold_by_lang[(p.get("language") or "unknown")].add((vid, pid))

    per_method: dict[str, dict[str, int]] = {}
    for method, results in methods_results.items():
        found: set[tuple[str, str]] = set()
        for vid, papers in results.items():
            for p in papers:
                pid = paper_id(p)
                if pid:
                    found.add((vid, pid))
        per_method[method] = {
            lang: len(pairs & found) for lang, pairs in sorted(gold_by_lang.items())
        }

    return {
        "gold_totals": {lang: len(pairs) for lang, pairs in sorted(gold_by_lang.items())},
        "found_by_method": per_method,
        "note": (
            "Only German and French carry non-English gold annotations; "
            "claims about other target languages are unsupported by this "
            "reference set. Language labels come from provider metadata."
        ),
    }


# ── Method-level overlap (replaces the tautological source-overlap matrix) ─


def method_overlap_analysis(
    methods_results: dict[str, dict[str, list[dict[str, Any]]]],
    gold: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Which methods find which gold papers (per-variant identity pairs)."""
    rel_found: dict[str, set[tuple[str, str]]] = {}
    for method, results in methods_results.items():
        found: set[tuple[str, str]] = set()
        for vid, papers in results.items():
            gold_ids = {paper_id(p) for p in gold[vid].get("relevant_papers") or []}
            gold_ids.discard(None)
            for p in papers:
                pid = paper_id(p)
                if pid and pid in gold_ids:
                    found.add((vid, pid))
        rel_found[method] = found

    pairwise: dict[str, dict[str, int]] = {}
    for a in rel_found:
        pairwise[a] = {}
        for b in rel_found:
            if a != b:
                pairwise[a][b] = len(rel_found[a] - rel_found[b])

    union_all = set().union(*rel_found.values()) if rel_found else set()
    return {
        "relevant_found_counts": {m: len(s) for m, s in rel_found.items()},
        "only_in_row_not_in_col": pairwise,
        "union_relevant_found": len(union_all),
        "note": (
            "Counts are unique (variant, paper) pairs. 'only_in_row_not_in_col' "
            "counts gold papers the row method finds that the column method "
            "misses; methods were separate live runs."
        ),
    }


def citation_exclusivity(
    collected: dict[str, dict[str, list[dict[str, Any]]]],
    nested: dict[str, list[dict[str, Any]]],
    gold: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Check whether citation-graph gold hits appear in ANY keyword list.

    Uses every keyword list recorded across all runs (M1, M2, M3, and the
    keyword phase of the recorded M4 run), not the post-deduplication
    source labels, so the exclusivity claim is not an artifact of
    first-seen attribution.
    """
    keyword_pairs: set[tuple[str, str]] = set()
    for method, results in collected.items():
        for vid, papers in results.items():
            papers_kw = papers
            if method == "full_pipeline":
                papers_kw, _ = split_full_pipeline(papers)
            for p in papers_kw:
                pid = paper_id(p)
                if pid:
                    keyword_pairs.add((vid, pid))

    cg_hits: list[dict[str, Any]] = []
    per_variant: dict[str, int] = defaultdict(int)
    n_exclusive = 0
    for vid, papers in nested.items():
        gold_ids = {paper_id(p) for p in gold[vid].get("relevant_papers") or []}
        gold_ids.discard(None)
        for p in papers:
            if p.get("source") != "citation_graph":
                continue
            pid = paper_id(p)
            if pid and pid in gold_ids:
                exclusive = (vid, pid) not in keyword_pairs
                n_exclusive += exclusive
                per_variant[vid] += 1
                cg_hits.append(
                    {
                        "variant_id": vid,
                        "gene": gold[vid]["gene"],
                        "title": p.get("title"),
                        "doi": p.get("doi"),
                        "year": p.get("year"),
                        "absent_from_all_keyword_lists": exclusive,
                    }
                )
    return {
        "n_relevant_citation_papers": len(cg_hits),
        "n_absent_from_all_keyword_lists": n_exclusive,
        "per_variant_counts": dict(per_variant),
        "papers": cg_hits,
    }


# ── Candidate-list integrity (replaces the former "dup rate" numbers) ──────


def candidate_list_integrity(
    methods_results: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    """Integrity statistics of the recorded candidate lists.

    Reports (a) within-variant duplicate identities remaining after the
    pipeline's deduplication (the actual dedup-effectiveness signal),
    (b) records lacking a DOI (metadata completeness -- the number the
    previously reported "duplication rates" actually measured), and
    (c) cross-variant repeated identities (the same paper returned for
    several variant queries, which is expected behavior, not duplication).
    This is NOT a dedup precision/recall evaluation; that would require the
    raw pre-deduplication provider streams, which the collection runs did
    not persist.
    """
    from collections import Counter

    out: dict[str, Any] = {}
    for method, results in methods_results.items():
        total = 0
        within_dupes = 0
        missing_doi = 0
        counter: Counter[str] = Counter()
        for papers in results.values():
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
        out[method] = {
            "total_entries": total,
            "within_variant_duplicate_ids": within_dupes,
            "missing_doi": missing_doi,
            "missing_doi_pct": 100.0 * missing_doi / total if total else 0.0,
            "cross_variant_repeated_ids": cross_repeats,
        }
    return out


# ── Fusion baselines over recorded runs (union / RRF) ──────────────────────


def fusion_analysis(
    m1_results: dict[str, list[dict[str, Any]]],
    m4n_results: dict[str, list[dict[str, Any]]],
    gold: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Fuse the recorded PubMed-only and nested-pipeline runs offline.

    Motivated by the observed channel disjointness: the auto-routing plan of
    the federated pipeline does not include the PubMed E-utilities channel
    (it queries PMC instead), so M1 and M4 recover largely different gold
    subsets. Two standard fusion strategies over the recorded ranked lists:

    - simple union (set-level; order-free)
    - Reciprocal Rank Fusion, RRF(d) = sum_lists 1/(60 + rank_d)
    """
    from metrics import compute_variant_metrics

    rrf_rows = []
    union_recalls: list[float] = []
    union_found = 0
    total_rel = 0
    for vid in sorted(m1_results):
        gold_papers = gold[vid].get("relevant_papers") or []
        gold_ids = {pid for p in gold_papers if (pid := paper_id(p))}
        total_rel += len(gold_ids)

        lists = [m1_results[vid], m4n_results[vid]]
        scores: dict[str, float] = defaultdict(float)
        first_seen: dict[str, dict[str, Any]] = {}
        for papers in lists:
            for rank, p in enumerate(papers, start=1):
                pid = paper_id(p)
                if not pid:
                    continue
                scores[pid] += 1.0 / (60 + rank)
                first_seen.setdefault(pid, p)
        fused = [first_seen[pid] for pid in sorted(scores, key=scores.get, reverse=True)]
        rrf_rows.append(compute_variant_metrics(gold[vid], fused, "fusion_rrf", DEFAULT_KS))

        union_ids = set(scores)
        found = len(union_ids & gold_ids)
        union_found += found
        union_recalls.append(found / len(gold_ids) if gold_ids else 0.0)

    rrf_agg = aggregate_metrics(rrf_rows, DEFAULT_KS)["fusion_rrf"]
    return {
        "channels": ["pubmed_only", NESTED_METHOD],
        "rrf_k": 60,
        "union": {
            "set_recall_macro": sum(union_recalls) / len(union_recalls),
            "set_recall_micro": union_found / total_rel if total_rel else 0.0,
            "relevant_found": union_found,
        },
        "rrf": rrf_agg,
        "note": (
            "Offline fusion of the two recorded runs; no new retrieval. "
            "Union is order-free; RRF produces a fused ranking evaluated "
            "like any other method."
        ),
    }


# ── Log parsing: provider failures + run window ────────────────────────────


def parse_collection_log(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False}
    text = path.read_text(errors="replace")
    stamps = re.findall(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text, re.M)
    fail_429 = re.findall(r"(\w+) search failed: Client error '429", text)
    per_provider_429: dict[str, int] = defaultdict(int)
    for prov in fail_429:
        per_provider_429[prov] += 1
    jstage_fail = len(re.findall(r"pyjstage search failed", text))
    translations = len(re.findall(r"query translated into 6 languages", text))
    cg_fail = len(re.findall(r"citation graph failed", text))
    return {
        "available": True,
        "log_file": path.name,
        "window_start": stamps[0] if stamps else None,
        "window_end": stamps[-1] if stamps else None,
        "http_429_by_provider": dict(per_provider_429),
        "http_429_total": len(fail_429),
        "jstage_parse_failures": jstage_fail,
        "citation_graph_failures": cg_fail,
        "translation_calls_logged": translations,
    }


# ── Main ───────────────────────────────────────────────────────────────────


def main() -> None:
    with open(COLLECTED_FILE) as f:
        collected = json.load(f)
    gold = load_gold()

    # 1. Strictly nested M4 (protocol-correct ablation), monotonicity check,
    #    and run-to-run variance. The variance measurement requires two
    #    INDEPENDENT executions of the same configuration; only the
    #    2026-08-12 session contains that duplicate (M3 vs the keyword phase
    #    of the M4' re-run). Later uniform-budget runs construct M4 by
    #    nesting on the frozen M3 lists, so variance is always read from the
    #    archived session when available.
    nested, nested_report = build_nested_full_pipeline(collected)
    monotonicity = verify_monotonicity(
        collected["multisource_with_translation"], nested, gold
    )
    if not monotonicity["holds"]:
        raise AssertionError(
            f"nested M4 violates recall monotonicity: {monotonicity['violations']}"
        )
    variance_source = collected
    variance_note = "current collection"
    archived = RESULTS_DIR / "collected_results_20260812.json"
    if archived.exists():
        variance_source = json.loads(archived.read_text())
        variance_note = "archived 2026-08-12 session (independent M3-config duplicate)"
    variance = run_variance_analysis(variance_source, gold)
    variance["source_note"] = variance_note

    with open(RESULTS_DIR / "derived_results.json", "w") as f:
        json.dump({NESTED_METHOD: nested}, f, ensure_ascii=False, indent=2)

    # Evaluation set: the three base runs + nested M4 (primary), plus the
    # recorded independent M4 re-run kept for the variance discussion.
    methods_results: dict[str, dict[str, list[dict[str, Any]]]] = {
        "pubmed_only": collected["pubmed_only"],
        "multisource_no_translation": collected["multisource_no_translation"],
        "multisource_with_translation": collected["multisource_with_translation"],
        NESTED_METHOD: nested,
        "full_pipeline": collected["full_pipeline"],
    }

    # 2. Per-variant metrics for every method.
    all_rows = []
    per_variant_metrics: dict[str, dict[str, Any]] = defaultdict(dict)
    for method, results in methods_results.items():
        for vid in sorted(results):
            m = compute_variant_metrics(gold[vid], results[vid], method, DEFAULT_KS)
            all_rows.append(m)
            per_variant_metrics[method][vid] = m
    with open(RESULTS_DIR / "metrics.json", "w") as f:
        json.dump([m.__dict__ for m in all_rows], f, ensure_ascii=False, indent=2)

    aggregates = aggregate_metrics(all_rows, DEFAULT_KS)

    # 3. Statistics: one declared family, Holm-corrected, bootstrap CIs.
    def series(method: str, metric: str, k: str | None = None) -> list[float]:
        rows = per_variant_metrics[method]
        vids = sorted(rows)
        if k is None:
            return [getattr(rows[v], metric) for v in vids]
        return [getattr(rows[v], metric)[k] for v in vids]

    tests: dict[str, dict[str, Any]] = {}

    def add_test(name: str, base: str, improved: str, metric: str, k: str | None) -> None:
        res = paired_test(series(base, metric, k), series(improved, metric, k))
        res.update({"base": base, "improved": improved, "metric": metric, "k": k})
        tests[name] = res

    # Declared family (8 distinct tests, Holm-corrected together).
    # Set-level recall: component chain + end-to-end. For M1/M2/M3 the
    # candidate budget is <= 20, so Recall@20 coincides with SetRecall and
    # separate @20 tests among them would double-count the same comparison.
    add_test("M2_vs_M1_set_recall", "pubmed_only", "multisource_no_translation", "set_recall", None)
    add_test("M3_vs_M2_set_recall", "multisource_no_translation", "multisource_with_translation", "set_recall", None)
    add_test("M4n_vs_M3_set_recall", "multisource_with_translation", NESTED_METHOD, "set_recall", None)
    add_test("M4n_vs_M1_set_recall", "pubmed_only", NESTED_METHOD, "set_recall", None)
    add_test("M4n_vs_M2_set_recall", "multisource_no_translation", NESTED_METHOD, "set_recall", None)
    # Fixed-cutoff recall (differs from set recall only for M4n, whose list
    # exceeds K=20).
    add_test("M4n_vs_M1_recall20", "pubmed_only", NESTED_METHOD, "recall_at", "20")
    # Ranking quality end-to-end and the precision cost of citation traversal.
    add_test("M4n_vs_M1_ndcg50", "pubmed_only", NESTED_METHOD, "ndcg_at", "50")
    add_test("M4n_vs_M3_prec_retr", "multisource_with_translation", NESTED_METHOD, "precision_at_retrieved", None)
    holm_correction(tests)

    # 4. Analyses.
    lang = language_analysis(methods_results, gold)
    overlap = method_overlap_analysis(methods_results, gold)
    cg_excl = citation_exclusivity(collected, nested, gold)
    integrity = candidate_list_integrity(methods_results)
    fusion = fusion_analysis(collected["pubmed_only"], nested, gold)
    log_stats = parse_collection_log(LOG_FILE)

    # Source coverage of the nested pipeline (first-seen attribution).
    src_relevant: dict[str, int] = defaultdict(int)
    src_all: dict[str, set[str]] = defaultdict(set)
    for vid, papers in nested.items():
        for p in papers:
            src = p.get("source") or "unknown"
            pid = paper_id(p)
            if pid:
                src_all[src].add(pid)
    for m in per_variant_metrics[NESTED_METHOD].values():
        for src, c in m.source_coverage.items():
            src_relevant[src] += c
    source_coverage = {
        src: {"relevant": src_relevant.get(src, 0), "retrieved_unique": len(ids)}
        for src, ids in sorted(src_all.items())
    }

    # 5. Gold-standard descriptive stats.
    lang_counts: dict[str, int] = defaultdict(int)
    evidence_counts: dict[str, int] = defaultdict(int)
    per_variant_gold: dict[str, Any] = {}
    for vid, v in sorted(gold.items()):
        papers = v.get("relevant_papers") or []
        for p in papers:
            lang_counts[p.get("language") or "unknown"] += 1
            evidence_counts[p.get("evidence_type") or "other"] += 1
        per_variant_gold[vid] = {
            "gene": v["gene"],
            "variant": v["variant"],
            "disease": v["disease"],
            "n_papers": len(papers),
        }

    # 6. Case studies: gold-relevant citation-graph findings per variant.
    case_studies: dict[str, Any] = {}
    for vid in ("V012", "V030", "V022"):
        hits = [p for p in cg_excl["papers"] if p["variant_id"] == vid]
        if hits:
            case_studies[vid] = {
                "gene": gold[vid]["gene"],
                "variant": gold[vid]["variant"],
                "disease": gold[vid]["disease"],
                "query": gold[vid]["query"],
                "cg_relevant": [
                    {"title": h["title"], "doi": h["doi"], "year": h["year"]}
                    for h in hits
                ],
            }

    # 7. Timing + external single-source baselines (if collected).
    timing = json.loads(TIMING_FILE.read_text()) if TIMING_FILE.exists() else None
    external = None
    sota_data = None
    sota_file = RESULTS_DIR / "sota_baselines.json"
    if sota_file.exists():
        sota_data = json.loads(sota_file.read_text())
    rerank_data = None
    rerank_file = RESULTS_DIR / "rerank_eval.json"
    if rerank_file.exists():
        rerank_data = json.loads(rerank_file.read_text())
    if EXTERNAL_FILE.exists():
        ext_raw = json.loads(EXTERNAL_FILE.read_text())
        ext_rows = []
        ext_lang: dict[str, dict[str, int]] = {}
        available: dict[str, bool] = {}
        for source, per_variant in ext_raw["results"].items():
            n_ok = sum(1 for v in per_variant.values() if v)
            available[source] = n_ok >= len(per_variant) * 0.9
            if not available[source]:
                continue
            for vid, papers in per_variant.items():
                ext_rows.append(compute_variant_metrics(gold[vid], papers, source, DEFAULT_KS))
        ext_agg = aggregate_metrics(ext_rows, DEFAULT_KS) if ext_rows else {}
        # Per-language gold recovery for available external sources.
        ext_methods = {
            s: r for s, r in ext_raw["results"].items() if available.get(s)
        }
        if ext_methods:
            ext_lang = language_analysis(ext_methods, gold)["found_by_method"]
        # Fusion over the available external per-source ranked lists.
        ext_fusion = None
        if len(ext_methods) >= 2:
            srcs = sorted(ext_methods)
            rrf_rows = []
            union_recalls = []
            union_found = 0
            total_rel = 0
            for vid in sorted(gold):
                gold_ids = {
                    pid for p in gold[vid].get("relevant_papers") or []
                    if (pid := paper_id(p))
                }
                total_rel += len(gold_ids)
                scores: dict[str, float] = defaultdict(float)
                first_seen: dict[str, dict[str, Any]] = {}
                for s in srcs:
                    for rank, p in enumerate(ext_methods[s].get(vid, []), start=1):
                        pid = paper_id(p)
                        if not pid:
                            continue
                        scores[pid] += 1.0 / (60 + rank)
                        first_seen.setdefault(pid, p)
                fused = [first_seen[pid] for pid in sorted(scores, key=scores.get, reverse=True)]
                rrf_rows.append(
                    compute_variant_metrics(gold[vid], fused, "external_rrf", DEFAULT_KS)
                )
                union_ids = set(scores)
                found = len(union_ids & gold_ids)
                union_found += found
                union_recalls.append(found / len(gold_ids) if gold_ids else 0.0)
            ext_fusion = {
                "sources": srcs,
                "rrf_k": 60,
                "union": {
                    "set_recall_macro": sum(union_recalls) / len(union_recalls),
                    "set_recall_micro": union_found / total_rel if total_rel else 0.0,
                    "relevant_found": union_found,
                },
                "rrf": aggregate_metrics(rrf_rows, DEFAULT_KS)["external_rrf"],
            }
        external = {
            "collected_at_utc": ext_raw.get("collected_at_utc"),
            "limit": ext_raw.get("limit"),
            "note": ext_raw.get("note"),
            "availability": available,
            "failures": {s: len(v) for s, v in ext_raw.get("failures", {}).items()},
            "aggregates": ext_agg,
            "language_recovery": ext_lang,
            "fusion": ext_fusion,
        }

    # 8. Run manifest for reproducibility.
    try:
        from lit_acquisition.config import get_config
        from lit_acquisition.query_translator import _SYSTEM_PROMPT  # type: ignore

        cfg = get_config()
        prompt_sha = hashlib.sha256(_SYSTEM_PROMPT.encode()).hexdigest()[:16]
    except Exception:
        cfg = None
        prompt_sha = None
    manifest = {
        "collection_date": "2026-08-14",
        "collection_window": {
            "start": log_stats.get("window_start"),
            "end": log_stats.get("window_end"),
        },
        "command": "python benchmarks/run_benchmark.py --collect --limit 50 --cg-budget 20",
        "candidate_budget_per_method": "50 keyword candidates per variant; "
        "citation traversal budget 20 papers, depth 1, direction both",
        "relevance_gate": "disabled during collection (raw retrieval comparison)",
        "translation": {
            "temperature": 0.2,
            "max_tokens": 1024,
            "target_languages": ["en", "zh", "ja", "de", "fr", "ru"],
            "system_prompt_sha256_16": prompt_sha,
            "model": (cfg.translation.model or cfg.llm.model) if cfg else None,
            "model_note": (
                "Recorded from environment config of the analysis run; the "
                "collection manifest (run_manifest.json) records the model "
                "of the actual collection run."
            ),
        },
        "statistics": {
            "test": "paired Wilcoxon signed-rank, two-sided, zeros dropped",
            "family_size": len(tests),
            "correction": "Holm-Bonferroni over the full declared family",
            "effect_size": "matched-pairs rank-biserial correlation (ties excluded)",
            "bootstrap": {"n": BOOT_N, "seed": BOOT_SEED, "type": "percentile, variant-level"},
        },
        "runs": "single collection run per method (no repeated trials); "
        "run-to-run variance estimated from the duplicated M3 configuration",
    }

    paper_data = {
        "schema_version": 2,
        "methods": METHOD_ORDER,
        "method_labels": METHOD_LABELS,
        "ks": list(DEFAULT_KS),
        "aggregates": aggregates,
        "tests_holm": tests,
        "nested_ablation": {
            "report": nested_report,
            "monotonicity": monotonicity,
        },
        "run_variance": variance,
        "language_analysis": lang,
        "method_overlap": overlap,
        "citation_exclusivity": {
            k: v for k, v in cg_excl.items() if k != "papers"
        },
        "source_coverage_nested": source_coverage,
        "candidate_list_integrity": integrity,
        "fusion": fusion,
        "provider_failures": log_stats,
        "gold_standard": {
            "n_variants": len(gold),
            "n_papers": sum(v["n_papers"] for v in per_variant_gold.values()),
            "languages": dict(sorted(lang_counts.items())),
            "evidence": dict(sorted(evidence_counts.items())),
            "per_variant": per_variant_gold,
        },
        "case_studies": case_studies,
        "timing": timing,
        "external_baselines": external,
        "sota_baselines": sota_data,
        "method_optimization": rerank_data,
        "run_manifest": manifest,
    }
    with open(RESULTS_DIR / "paper_data.json", "w") as f:
        json.dump(paper_data, f, ensure_ascii=False, indent=2)

    # 9. Human-readable summary.
    lines = ["# Benchmark Results (corrected protocol)\n"]
    lines.append("## Fixed-K metrics (K=20) + set-level metrics\n")
    lines.append(format_summary_table(aggregates, k=20))
    lines.append("\n\n## Fixed-K metrics (K=10)\n")
    lines.append(format_summary_table(aggregates, k=10))
    lines.append("\n\n## Paired Wilcoxon tests (Holm-corrected family)\n")
    lines.append("| Test | Δ | p | p(Holm) | r | 95% CI | sig |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for name, t in tests.items():
        lines.append(
            f"| {name} | {t['delta']:+.3f} | {t['p']:.4f} | {t['p_holm']:.4f} "
            f"| {t['r']:+.2f} | [{t['ci95'][0]:+.3f}, {t['ci95'][1]:+.3f}] "
            f"| {'yes' if t['significant_holm'] else 'no'} |"
        )
    lines.append("\n\n## Run-to-run variance (duplicated M3 configuration)\n")
    lines.append(
        f"- identical candidate lists: {variance['n_identical_lists']}/{variance['n_variants']}\n"
        f"- mean Jaccard: {variance['mean_jaccard']:.3f} (min {variance['min_jaccard']:.3f})\n"
        f"- mean |Δ set recall|: {variance['mean_abs_set_recall_diff']:.4f} "
        f"(max {variance['max_abs_set_recall_diff']:.4f})\n"
    )

    # 9b. 2025-2026 SOTA baselines (if collected; frozen M1-M4 untouched).
    if sota_data:
        sota_labels = sota_data.get("methods", {})
        agg_s = sota_data["aggregates"]
        lines.append("\n## 2025-2026 SOTA baselines (collected 2026-08-14)\n")
        lines.append("## Fixed-K metrics (K=20) + set-level metrics\n")
        sota_order = ["pubmed_only", NESTED_METHOD, *sota_data["methods"]]
        rows = []
        for m in sota_order:
            a = agg_s[m]
            rows.append(
                f"| {sota_labels.get(m, m)} | "
                f"{a['recall_at']['20']['mean']:.3f}±{a['recall_at']['20']['std']:.3f} | "
                f"{a['precision_at']['20']['mean']:.3f}±{a['precision_at']['20']['std']:.3f} | "
                f"{a['ndcg_at']['20']['mean']:.3f}±{a['ndcg_at']['20']['std']:.3f} | "
                f"{a['set_recall']['mean']:.3f}±{a['set_recall']['std']:.3f} | "
                f"{a['precision_at_retrieved']['mean']:.3f}±{a['precision_at_retrieved']['std']:.3f} | "
                f"{a['candidates_per_variant']['mean']:.1f} | "
                f"{a['micro']['relevant_found']} |"
            )
        lines.append("| Method | Recall@20 | Prec@20 | nDCG@20 | SetRecall | Prec@Retr | Cands | Rel. |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        lines.extend(rows)
        lines.append("\n## SOTA paired tests vs M1 / M4n (SetRecall, Holm family)\n")
        lines.append("| Test | Δ | p | p(Holm) | r | 95% CI | sig |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for name, t in sota_data["tests_holm"].items():
            lines.append(
                f"| {name} | {t['delta']:+.3f} | {t['p']:.4f} | {t['p_holm']:.4f} "
                f"| {t['r']:+.2f} | [{t['ci95'][0]:+.3f}, {t['ci95'][1]:+.3f}] "
                f"| {'yes' if t['significant_holm'] else 'no'} |"
            )
    # 9c. Neural-rerank optimization experiment (offline, frozen lists).
    if rerank_data:
        agg_r = rerank_data["aggregates"]
        labels_r = {"full_pipeline_nested": "M4n baseline",
                    "reranked": "M5 neural rerank", "gated": "M5g gated citation"}
        lines.append("\n## Neural-rerank optimization (offline, on frozen nested lists)\n")
        lines.append("| Method | Recall@20 | Prec@20 | nDCG@20 | Prec@Retr | SetRecall | Rel. | Cands |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for m in ("full_pipeline_nested", "reranked", "gated"):
            a = agg_r[m]
            lines.append(
                f"| {labels_r[m]} | "
                f"{a['recall_at']['20']['mean']:.3f}±{a['recall_at']['20']['std']:.3f} | "
                f"{a['precision_at']['20']['mean']:.3f}±{a['precision_at']['20']['std']:.3f} | "
                f"{a['ndcg_at']['20']['mean']:.3f}±{a['ndcg_at']['20']['std']:.3f} | "
                f"{a['precision_at_retrieved']['mean']:.3f}±{a['precision_at_retrieved']['std']:.3f} | "
                f"{a['set_recall']['mean']:.3f}±{a['set_recall']['std']:.3f} | "
                f"{a['micro']['relevant_found']} | {a['candidates_per_variant']['mean']:.1f} |"
            )
        lines.append(f"\nCitation gating: kept {rerank_data['citation_items']['kept_by_gate']}/"
                     f"{rerank_data['citation_items']['total']} items, relevant found unchanged.\n")
    (RESULTS_DIR / "summary.md").write_text("\n".join(lines))

    print(f"paper_data.json, metrics.json, derived_results.json, summary.md -> {RESULTS_DIR}")
    if rerank_data:
        print("\nOptimization (M5/M5g vs M4n):")
        for m in ("reranked", "gated"):
            a = agg_r[m]
            print(f"  {labels_r[m]:<22} R@20 {a['recall_at']['20']['mean']:.3f} "
                  f"nDCG@20 {a['ndcg_at']['20']['mean']:.3f} Prec@Retr "
                  f"{a['precision_at_retrieved']['mean']:.3f} (SetRecall {a['set_recall']['mean']:.3f})")
    if sota_data:
        print("\nSOTA baselines table (SetRecall, mean±std):")
        for m in sota_data["methods"]:
            a = agg_s[m]
            print(f"  {sota_labels[m]:<28} {a['set_recall']['mean']:.3f}±{a['set_recall']['std']:.3f} "
                  f"({a['micro']['relevant_found']} relevant)")
    print(f"\nMonotonicity: holds={monotonicity['holds']}, "
          f"improved={monotonicity['n_variants_improved']} variants")
    print(f"Run variance: identical {variance['n_identical_lists']}/30, "
          f"mean Jaccard {variance['mean_jaccard']:.3f}, "
          f"mean |dR| {variance['mean_abs_set_recall_diff']:.4f}, "
          f"max {variance['max_abs_set_recall_diff']:.4f}")
    print("\n" + format_summary_table(aggregates, k=20))
    print("\nTests (Holm):")
    for name, t in tests.items():
        print(f"  {name}: d={t['delta']:+.4f} p={t['p']:.4f} pH={t['p_holm']:.4f} "
              f"r={t['r']:+.2f} CI=[{t['ci95'][0]:+.3f},{t['ci95'][1]:+.3f}] "
              f"{'SIG' if t['significant_holm'] else 'ns'}")


if __name__ == "__main__":
    main()
