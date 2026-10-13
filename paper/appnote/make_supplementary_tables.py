"""Generate the supplementary tables from the benchmark artifacts.

Every table is written to ``tables/*.tex`` and included by
``supplementary.tex``. Numbers are read from
``benchmarks/results/paper_data.json`` so the supplement cannot drift from the
artifacts; run ``check_numbers.py`` afterwards to verify the manuscript.

Usage::

    python paper/appnote/make_supplementary_tables.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PAPER_DATA = ROOT / "benchmarks" / "results" / "paper_data.json"
TABLES = HERE / "tables"

METHODS = [
    ("pubmed_only", "M1 PubMed-only"),
    ("multisource_no_translation", "M2 multi-source"),
    ("multisource_with_translation", "M3 +translation"),
    ("full_pipeline_nested", "M4n +citation (nested)"),
]
KS = ["5", "10", "15", "20", "50"]


def _metrics_table(data: dict) -> str:
    lines = [
        r"\begin{table}[h]",
        r"\centering\small",
        r"\caption{Full per-method metrics over 43 variants (macro means). "
        r"Precision@$K$ uses a fixed denominator $K$; Precision@Retr.\ is over the retrieved list.}",
        r"\label{tab:supp-metrics}",
        r"\begin{tabular}{l" + "c" * 7 + "}",
        r"\toprule",
        r"Method & SetRec. & Rec@5 & Rec@20 & Rec@50 & P@20 & nDCG@20 & nDCG@50 \\",
        r"\midrule",
    ]
    for key, label in METHODS:
        agg = data["aggregates"][key]
        row = [
            label,
            f"{agg['set_recall']['mean']:.3f}",
            f"{agg['recall_at']['5']['mean']:.3f}",
            f"{agg['recall_at']['20']['mean']:.3f}",
            f"{agg['recall_at']['50']['mean']:.3f}",
            f"{agg['precision_at']['20']['mean']:.3f}",
            f"{agg['ndcg_at']['20']['mean']:.3f}",
            f"{agg['ndcg_at']['50']['mean']:.3f}",
        ]
        lines.append(" & ".join(row) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def _significance_table(data: dict) -> str:
    lines = [
        r"\begin{table}[h]",
        r"\centering\small",
        r"\caption{Pre-declared eight-test family: paired Wilcoxon signed-rank with "
        r"Holm--Bonferroni correction, 10{,}000 bootstrap resamples (seed 42). "
        r"$r$ is the matched-pairs rank-biserial correlation.}",
        r"\label{tab:supp-tests}",
        r"\begin{tabular}{lcccc}",
        r"\toprule",
        r"Contrast & $\Delta$ & 95\% CI & $p_{\mathrm{Holm}}$ & $r$ \\",
        r"\midrule",
    ]
    for name, test in data["tests_holm"].items():
        low, high = test["ci95"]
        lines.append(
            f"{name.replace('_', ' ')} & {test['delta']:+.3f} & "
            f"[{low:+.3f}, {high:+.3f}] & {test['p_holm']:.2e} & {test['r']:+.2f} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def _sota_table(data: dict) -> str:
    sota = data["sota_baselines"]
    lines = [
        r"\begin{table}[h]",
        r"\centering\small",
        r"\caption{Single-channel baselines (uniform 20-paper budget over the same "
        r"PubMed candidate pool unless noted). Significance is versus M4n.}",
        r"\label{tab:supp-sota}",
        r"\begin{tabular}{lccc}",
        r"\toprule",
        r"Method & SetRecall & Recall@20 & $p_{\mathrm{Holm}}$ vs M4n \\",
        r"\midrule",
    ]
    order = ["sota_dense", "sota_rerank", "sota_hybrid", "sota_expand", "sota_agent", "sota_mdense"]
    for key in order:
        agg = sota["aggregates"][key]
        test = sota["tests_holm"].get(f"{key}_vs_full_pipeline_nested_set_recall", {})
        lines.append(
            f"{sota['methods'][key]} & {agg['set_recall']['mean']:.3f} & "
            f"{agg['recall_at']['20']['mean']:.3f} & "
            f"{test.get('p_holm', float('nan')):.2e} \\\\"
        )
    pipeline = data["aggregates"]["full_pipeline_nested"]
    lines.append(r"\midrule")
    lines.append(
        f"\\textbf{{M4n pipeline}} & \\textbf{{{pipeline['set_recall']['mean']:.3f}}} & "
        f"\\textbf{{{pipeline['recall_at']['20']['mean']:.3f}}} & --- \\\\"
    )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def _language_table(data: dict) -> str:
    lang = data["language_analysis"]
    totals = lang["gold_totals"]
    lines = [
        r"\begin{table}[h]",
        r"\centering\small",
        r"\caption{Annotated German/French reference papers recovered per method. "
        r"Only these two languages carry non-English annotations.}",
        r"\label{tab:supp-language}",
        r"\begin{tabular}{lccc}",
        r"\toprule",
        r"Method & German (of " + str(totals["de"]) + r") & French (of " + str(totals["fr"]) + r") & Total (of "
        + str(totals["de"] + totals["fr"]) + r") \\",
        r"\midrule",
    ]
    for key, label in METHODS:
        found = lang["found_by_method"][key]
        lines.append(f"{label} & {found['de']} & {found['fr']} & {found['de'] + found['fr']} \\\\")
    lines += [
        r"\midrule",
        r"Best English-only baseline (S3 hybrid) & 1 & 0 & 1 \\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    return "\n".join(lines) + "\n"


def _failure_table(data: dict) -> str:
    failures = data["provider_failures"]
    lines = [
        r"\begin{table}[h]",
        r"\centering\small",
        r"\caption{Provider failures recorded during the collection session "
        r"(window " + failures["window_start"] + r" to " + failures["window_end"] + r").}",
        r"\label{tab:supp-failures}",
        r"\begin{tabular}{lc}",
        r"\toprule",
        r"Signal & Count \\",
        r"\midrule",
        f"Semantic Scholar HTTP 429 & {failures['http_429_by_provider'].get('semantic_scholar', 0)} \\\\",
        f"HTTP 429 (all providers) & {failures['http_429_total']} \\\\",
        f"J-STAGE parse failures & {failures['jstage_parse_failures']} \\\\",
        f"Citation-graph failures & {failures['citation_graph_failures']} \\\\",
        f"Translation calls logged & {failures['translation_calls_logged']} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    data = json.loads(PAPER_DATA.read_text(encoding="utf-8"))
    TABLES.mkdir(exist_ok=True)
    outputs = {
        "supp_metrics.tex": _metrics_table(data),
        "supp_tests.tex": _significance_table(data),
        "supp_sota.tex": _sota_table(data),
        "supp_language.tex": _language_table(data),
        "supp_failures.tex": _failure_table(data),
    }
    for name, content in outputs.items():
        (TABLES / name).write_text(content, encoding="utf-8")
        print(f"wrote tables/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
