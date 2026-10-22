"""Verify that every headline number in the Applications Note matches the artifacts.

Implements the traceability rule from ``paper/BIOINFORMATICS_PLAN.md`` (section
4): numbers are copied from artifacts, never retyped from memory. This script
recomputes each claim from ``benchmarks/results/*.json`` and asserts that the
expected token appears in ``main.tex`` -- so a stale manuscript fails loudly.

Usage::

    python paper/appnote/check_numbers.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = ROOT / "benchmarks" / "results"
PAPER_DATA = RESULTS / "paper_data.json"
TIMING = RESULTS / "timing_43variants.json"
THROUGHPUT = RESULTS / "download_throughput.json"
TEX = HERE / "main.tex"
SUPP = HERE / "supplementary.tex"
HARDENING_TEX = HERE / "tables" / "supp_hardening.tex"

# Artifacts behind tables/supp_hardening.tex; the fragment must not be older
# than any of them, otherwise the supplement quotes stale numbers.
HARDENING_SOURCES = (
    "expanded_reference.json",
    "m2_deconfounded.json",
    "language_analysis_backfilled.json",
    "clinvar_ground_truth.json",
    "clinvar_eval.json",
    "sota_fair_pool.json",
    "rerank_eval_corrected.json",
    "citation_seeds.json",
    "gate_eval.json",
)


def _normalized(text: str) -> str:
    """Collapse whitespace so checks are independent of line wrapping."""
    return re.sub(r"\s+", " ", text)


def check_hardening(checks: list[tuple[str, str, bool]]) -> None:
    """Verify the plan's hardening artifacts against the manuscripts."""

    def load(name: str) -> dict | None:
        path = RESULTS / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def check(label: str, needle: str, ok: bool) -> None:
        checks.append((label, needle, ok))

    main = _normalized(TEX.read_text(encoding="utf-8"))
    # supplementary.tex pulls the generated fragment in with \input, so the
    # numbers a reader sees live in the fragment as well.
    supp = _normalized(SUPP.read_text(encoding="utf-8"))
    if HARDENING_TEX.exists():
        supp += " " + _normalized(HARDENING_TEX.read_text(encoding="utf-8"))

    # The generated fragment must exist and be included by the supplement.
    check("hardening fragment included", "tables/supp_hardening.tex",
          HARDENING_TEX.exists() and "tables/supp_hardening.tex" in supp)

    # Freshness: every source artifact must be older than the fragment.
    if HARDENING_TEX.exists():
        fragment_mtime = HARDENING_TEX.stat().st_mtime
        stale = [name for name in HARDENING_SOURCES
                 if (RESULTS / name).exists() and (RESULTS / name).stat().st_mtime > fragment_mtime]
        check("hardening fragment fresh", "no stale sources", not stale)
        if stale:
            print("stale hardening sources (run make_review_hardening.py):", ", ".join(stale))

    # ── E4a language backfill ─────────────────────────────────────────────
    backfill = load("language_analysis_backfilled.json")
    if backfill:
        after = backfill["gold_totals_after"]
        before = backfill["gold_totals_before"]
        heldout = backfill["validation"]["heldout_half_adjudicated"]
        check("E4a German before/after in main",
              f"{before['de']} to {after['de']}", f"from {before['de']} to {after['de']}" in main)
        check("E4a French before/after in main",
              f"{before['fr']} to {after['fr']}", f"from {before['fr']} to {after['fr']}" in main)
        check("E4a held-out accuracy in supplement",
              f"{heldout['accuracy']:.3f}", f"{heldout['accuracy']:.3f}" in supp)
        check("E4a provider-label noise count in supplement",
              str(backfill["validation"]["provider_label_noise"]["n"]),
              f"{backfill['validation']['provider_label_noise']['n']} provider labels" in supp)

    # ── E1 deconfounding ──────────────────────────────────────────────────
    e1 = load("m2_deconfounded.json")
    if e1:
        agg = e1["aggregates_frozen"]
        tests = e1["tests_holm_frozen"]
        check("E1 M2_lex Recall@20 in supplement",
              f"{agg['m2_lex']['recall_at']['20']['mean']:.3f}",
              f"{agg['m2_lex']['recall_at']['20']['mean']:.3f}" in supp)
        delta = tests["translation_lexical_rung_recall20"]["delta"]
        check("E1 translation (lexical rung) delta in supplement",
              f"{delta:+.3f}", f"{delta:+.3f}" in supp)

    # ── E2c pooled expansion ──────────────────────────────────────────────
    expanded = load("expanded_reference.json")
    if expanded:
        added = expanded["sizes"]["added_total"]
        check("E2c added papers in supplement", str(added), f"{added} further papers" in supp)

    # ── E2a ClinVar truth ─────────────────────────────────────────────────
    truth = load("clinvar_ground_truth.json")
    clinvar_eval = load("clinvar_eval.json")
    if truth and clinvar_eval:
        n_var = truth["totals"]["variants_with_records"]
        papers = truth["totals"]["cited_papers_after_filter"]
        check("E2a variants with records in supplement", str(n_var), f"{n_var} of 43 variants" in supp)
        check("E2a curated citation count in supplement", f"{papers:,}",
              f"{papers:,} curated citations" in supp)
        median = clinvar_eval["temporal_profile"]["truth"]["median_year"]
        check("E2a median truth year in supplement", str(median),
              f"median year {median}" in supp)

    # ── E3 unbound-pool SOTA ──────────────────────────────────────────────
    e3 = load("sota_fair_pool.json")
    if e3:
        frozen = e3["aggregates_frozen"]
        for method in ("t1_hybrid_rrf", "m2_lex"):
            value = f"{frozen[method]['recall_at']['20']['mean']:.3f}"
            check(f"E3 {method} Recall@20 in supplement", value, value in supp)

    # ── E4c reranking + gating + seeds ────────────────────────────────────
    corrected = load("rerank_eval_corrected.json")
    if corrected:
        gate = corrected["gate_statistics"]
        check("E4c gate kept/total in supplement", f"{gate['kept_by_gate']}",
              f"keeping {gate['kept_by_gate']} of {gate['citation_items']} citation items" in supp)
        delta = corrected["tests_holm_frozen"]["m5g_gated_vs_m4n_precision_at_retrieved_retr"]["delta"]
        check("E4c gated precision delta in supplement", f"{delta:+.4f}", f"{delta:+.4f}" in supp)
    seeds = load("citation_seeds.json")
    if seeds:
        k1 = seeds["contributions"]["k1"]["papers_appended"]
        k5 = seeds["contributions"]["k5"]["papers_appended"]
        check("E4c seed k=1 appended in supplement", str(k1), f"k=1 appends {k1}" in supp)
        check("E4c seed k=5 appended in supplement", str(k5), f"k=5 appends {k5}" in supp)

    # ── E4b relevance gate (skipped when the provider was unavailable) ─────
    gate_eval = load("gate_eval.json")
    if gate_eval and gate_eval.get("status") == "ok":
        metrics = gate_eval["metrics"]
        check("E4b precision before in supplement",
              f"{metrics['precision_at_retrieved_before']:.3f}",
              f"{metrics['precision_at_retrieved_before']:.3f}" in supp)
        check("E4b false-negative rate in supplement",
              f"{metrics['false_negative_rate']:.3f}",
              f"{metrics['false_negative_rate']:.3f}" in supp)

    # ── E2b annotation package ────────────────────────────────────────────
    sample = ROOT / "benchmarks" / "annotation" / "e2b_sample.csv"
    if sample.exists():
        rows = sample.read_text(encoding="utf-8").strip().splitlines()[1:]
        variants = len({row.split(",")[1] for row in rows})
        check("E2b sample size in supplement", str(len(rows)), f"{len(rows)} papers" in supp)
        check("E2b variant count in supplement", str(variants), f"{variants} variants" in supp)


def main() -> int:
    data = json.loads(PAPER_DATA.read_text(encoding="utf-8"))
    timing = json.loads(TIMING.read_text(encoding="utf-8"))
    throughput = json.loads(THROUGHPUT.read_text(encoding="utf-8"))
    tex = TEX.read_text(encoding="utf-8")

    aggregates = data["aggregates"]
    tests = data["tests_holm"]
    gold = data["gold_standard"]
    citation = data["citation_exclusivity"]
    languages = data["language_analysis"]
    sota = data["sota_baselines"]
    composed = timing["composed_full_pipeline"]
    download = throughput["aggregate"]

    def f(value: float, digits: int = 3) -> str:
        return f"{value:.{digits}f}"

    checks: list[tuple[str, str, bool]] = []

    def check(label: str, needle: str) -> None:
        checks.append((label, needle, needle in tex))

    # --- reference set -----------------------------------------------------
    check("n variants", str(gold["n_variants"]))
    check("n papers", str(gold["n_papers"]))
    check("german gold", str(languages["gold_totals"]["de"]))
    check("french gold", str(languages["gold_totals"]["fr"]))
    # 305 unlabelled papers are now reported in the supplementary hardening
    # section (the main text only carries the backfilled buckets).
    supp_head = _normalized(SUPP.read_text(encoding="utf-8"))
    checks.append(("unknown language", str(gold["languages"]["unknown"]),
                   str(gold["languages"]["unknown"]) in supp_head))

    # --- headline metrics --------------------------------------------------
    for key, label in (
        ("pubmed_only", "M1"),
        ("multisource_no_translation", "M2"),
        ("multisource_with_translation", "M3"),
        ("full_pipeline_nested", "M4n"),
    ):
        check(f"{label} SetRecall", f(aggregates[key]["set_recall"]["mean"]))
        check(f"{label} Recall@20", f(aggregates[key]["recall_at"]["20"]["mean"]))
        check(f"{label} nDCG@20", f(aggregates[key]["ndcg_at"]["20"]["mean"]))
        check(f"{label} relevant found", str(aggregates[key]["micro"]["relevant_found"]))

    # --- significance ------------------------------------------------------
    delta = tests["M4n_vs_M1_set_recall"]
    check("M4n-M1 delta", f"+{delta['delta']:.3f}")
    check("CI lower", f"+{delta['ci95'][0]:.3f}")
    check("CI upper", f"+{delta['ci95'][1]:.3f}")
    check("rank-biserial", f"{delta['r']:.2f}")
    check("M2-M1 delta", f"+{tests['M2_vs_M1_set_recall']['delta']:.3f}")
    check("Recall@20 delta", f"+{tests['M4n_vs_M1_recall20']['delta']:.3f}")
    check("nDCG@50 delta", f"+{tests['M4n_vs_M1_ndcg50']['delta']:.3f}")
    check("precision cost", f"{abs(tests['M4n_vs_M3_prec_retr']['delta']):.3f}")

    # --- multilingual ------------------------------------------------------
    found = languages["found_by_method"]["full_pipeline_nested"]
    recovered = found["de"] + found["fr"]
    check("de+fr recovered", f"{recovered} of the {languages['gold_totals']['de'] + languages['gold_totals']['fr']}")
    check("german recovered", f"{found['de']}/{languages['gold_totals']['de']}")
    check("french recovered", f"{found['fr']}/{languages['gold_totals']['fr']}")

    # --- citation ----------------------------------------------------------
    words = {1: "one", 2: "two", 3: "three"}
    assert citation["n_relevant_citation_papers"] == 2, "artifact changed; update the wording check"
    check("citation relevant total", f"{words[citation['n_relevant_citation_papers']]} relevant papers")
    assert citation["n_absent_from_all_keyword_lists"] == 1, "artifact changed; update the wording check"
    check("citation keyword-exclusive", "no keyword list at all")
    check("citation exclusive", "V002")
    check("citation exclusive 2", "V035")

    # --- SOTA --------------------------------------------------------------
    hybrid = sota["aggregates"]["sota_hybrid"]["set_recall"]["mean"]
    check("hybrid set recall", f"({hybrid:.3f})")
    check("pool mean", f"{sota['realized_mean_candidates']['sota_dense']:.1f}")
    hybrid_test = sota["tests_holm"]["sota_hybrid_vs_full_pipeline_nested_set_recall"]
    check("hybrid raw p", f"{hybrid_test['p']:.2f}")
    check("hybrid holm-adjusted p", f"{hybrid_test['p_holm']:.2f}")

    # --- channels (rendered by the figure script, not in the prose) --------
    figure_script = (HERE / "make_figure1.py").read_text(encoding="utf-8")
    checks.append(("figure reads channel coverage", "source_coverage_nested", "source_coverage_nested" in figure_script))
    figure_pdf = HERE / "figure1.pdf"
    checks.append(("figure built", "figure1.pdf", figure_pdf.exists()))
    if figure_pdf.exists() and figure_pdf.stat().st_mtime < PAPER_DATA.stat().st_mtime:
        print("warning: figure1.pdf is older than paper_data.json - regenerate with make_figure1.py")

    # --- cost --------------------------------------------------------------
    check("median pipeline seconds", f"{composed['median_s']:.2f}")
    check("p90 pipeline seconds", f"{composed['p90_s']:.2f}")
    check("pdfs per minute", f"{download['pdfs_per_minute']:.2f}")
    check("pdf success rate", f"{download['pdf_success_rate'] * 100:.1f}")
    check("seconds per pdf", f"{download['seconds_per_pdf']:.2f}")
    check("peak rss", f"{download['peak_rss_mb']:.1f}")

    check_hardening(checks)

    failed = [row for row in checks if not row[2]]
    for label, needle, ok in checks:
        print(f"{'OK  ' if ok else 'FAIL'}  {label:24s} -> {needle}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    if failed:
        print("FAILED:", ", ".join(label for label, _, _ in failed))
        return 1

    # sanity: no obvious placeholder leakage into numbers
    for pattern in (r"\bTODO\b", r"\bXXX\b", r"\?\?\?"):
        if re.search(pattern, tex):
            print(f"note: placeholder pattern {pattern!r} present in main.tex")
    return 0


if __name__ == "__main__":
    sys.exit(main())
