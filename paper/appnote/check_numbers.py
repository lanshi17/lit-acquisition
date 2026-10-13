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
PAPER_DATA = ROOT / "benchmarks" / "results" / "paper_data.json"
TIMING = ROOT / "benchmarks" / "results" / "timing_43variants.json"
THROUGHPUT = ROOT / "benchmarks" / "results" / "download_throughput.json"
TEX = HERE / "main.tex"


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
    check("unknown language", str(gold["languages"]["unknown"]))

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
