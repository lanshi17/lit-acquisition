"""Generate publication-quality figures for the JCDL paper.

Outputs to benchmarks/paper_figures/:
  fig2_source_coverage.png/.pdf   — unique papers per source
  fig3_ablation_recall.png/.pdf   — ablation recall/P1/nDCG bar chart
  fig3_multilingual.png/.pdf      — per-language relevant papers found
  fig4_citation_graph.png/.pdf    — citation graph vs keyword contribution
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import scienceplots  # noqa: F401  (registers 'science'/'ieee' styles)

import matplotlib.pyplot as plt
import numpy as np

plt.style.use(["science", "ieee", "no-latex"])

RESULTS_DIR = Path("benchmarks/results")
FIG_DIR = Path("benchmarks/paper_figures")
FIG_DIR.mkdir(parents=True, exist_ok=True)

# ── Load data ──────────────────────────────────────────────────────────────
with open(RESULTS_DIR / "collected_results.json") as f:
    results = json.load(f)

with open(RESULTS_DIR / "metrics.json") as f:
    metrics = json.load(f)


def method_metrics(method: str) -> list[dict]:
    """Return per-variant metrics for a method."""
    return [m for m in metrics if m.get("method") == method and m.get("recall", 0) > 0]


def recall_of(method: str, vid: str) -> float:
    m = next((x for x in metrics if x["method"] == method and x["variant_id"] == vid), None)
    return m["recall"] if m else 0.0


METHODS = ["pubmed_only", "multisource_no_translation", "multisource_with_translation", "full_pipeline"]
METHOD_LABELS = {
    "pubmed_only": "M1: PubMed",
    "multisource_no_translation": "M2: Multi-source",
    "multisource_with_translation": "M3: +Translation",
    "full_pipeline": "M4: +Citation graph",
}

# ── Figure 3a: Ablation bar chart ──────────────────────────────────────────
def fig_ablation() -> None:
    agg = {m: {"recall": [], "precision": [], "ndcg": []} for m in METHODS}
    for m in metrics:
        if m["method"] in agg:
            agg[m["method"]]["recall"].append(m["recall"])
            agg[m["method"]]["precision"].append(m["precision"])
            agg[m["method"]]["ndcg"].append(m["ndcg"])

    metrics_names = ["Recall@50", "Precision@50", "nDCG@50"]
    colors = ["#4C72B0", "#DD8452", "#55A868"]
    x = np.arange(len(METHODS))
    width = 0.25

    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    for i, (metric, color) in enumerate(zip(metrics_names, colors)):
        vals = [np.mean(agg[m][metric_key(metric)]) for m in METHODS]
        errs = [np.std(agg[m][metric_key(metric)]) for m in METHODS]
        ax.bar(x + (i - 1) * width, vals, width, label=metric, color=color, alpha=0.85,
               yerr=errs, capsize=3, error_kw={"linewidth": 0.8})
    ax.set_xticks(x)
    ax.set_xticklabels([METHOD_LABELS[m] for m in METHODS], rotation=15, ha="right")
    ax.set_ylabel("Score")
    ax.set_ylim(0, 0.55)
    ax.legend(frameon=True, fontsize=8, loc="upper left")
    ax.set_title("Ablation Study: Component Contributions")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig3_ablation.png", dpi=300)
    fig.savefig(FIG_DIR / "fig3_ablation.pdf")
    plt.close(fig)
    print("✓ fig3_ablation.png")


def metric_key(name: str) -> str:
    return {"Recall@50": "recall", "Precision@50": "precision", "nDCG@50": "ndcg"}[name]


# ── Figure 2: Source coverage ──────────────────────────────────────────────
def fig_source_coverage() -> None:
    # unique papers per source for full_pipeline
    unique = defaultdict(set)
    for vid, papers in results["full_pipeline"].items():
        for p in papers:
            doi = (p.get("doi") or "").lower().strip()
            if doi:
                unique[p.get("source", "?")].add(doi)

    sources = ["pmc", "crossref", "citation_graph", "europepmc"]
    labels = ["PMC", "Crossref", "Citation graph", "EuropePMC"]
    counts = [len(unique.get(s, set())) for s in sources]
    colors = ["#4C72B0", "#DD8452", "#55A868", "#C44E52"]

    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    bars = ax.bar(labels, counts, color=colors, alpha=0.85, edgecolor="black", linewidth=0.5)
    ax.set_ylabel("Unique papers (by DOI)")
    ax.set_title("Source Coverage: Unique Papers per Source")
    for bar, c in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 3, str(c),
                ha="center", va="bottom", fontsize=9)
    ax.set_ylim(0, max(counts) * 1.15)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig2_source_coverage.png", dpi=300)
    fig.savefig(FIG_DIR / "fig2_source_coverage.pdf")
    plt.close(fig)
    print("✓ fig2_source_coverage.png")


# ── Figure 3b: Multilingual contribution ───────────────────────────────────
def fig_multilingual() -> None:
    # Languages with relevant papers across M3/M4
    combos = Counter()
    for method in ["multisource_with_translation", "full_pipeline"]:
        for vid, papers in results[method].items():
            for p in papers:
                lang = p.get("language") or "unknown"
                combos[lang] += 1

    langs = ["en", "de", "fr", "zh", "unknown"]
    labels = ["English", "German", "French", "Chinese", "Unknown"]
    counts = [combos.get(l, 0) for l in langs]
    found = [l for l, c in zip(labels, counts) if c > 0]

    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    ax.bar(found, [c for l, c in zip(labels, counts) if c > 0],
           color=["#4C72B0", "#DD8452", "#55A868", "#8172B3", "#CCB974"],
           alpha=0.85, edgecolor="black", linewidth=0.5)
    ax.set_ylabel("Papers retrieved")
    ax.set_title("Multilingual Search: Papers by Query Language")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig3_multilingual.png", dpi=300)
    fig.savefig(FIG_DIR / "fig3_multilingual.pdf")
    plt.close(fig)
    print("✓ fig3_multilingual.png")


# ── Figure 4: Citation graph contribution ─────────────────────────────────
def fig_citation_graph() -> None:
    # Papers from citation graph per variant (top 10)
    cg_per_variant = []
    for vid, papers in results["full_pipeline"].items():
        n = sum(1 for p in papers if p.get("source") == "citation_graph")
        gene = ""
        with open("benchmarks/gold_standard/variants.jsonl") as f:
            for line in f:
                v = json.loads(line.strip())
                if v["variant_id"] == vid:
                    gene = v["gene"]
                    break
        cg_per_variant.append((gene, n))

    cg_per_variant.sort(key=lambda x: -x[1])
    top = cg_per_variant[:12]
    genes = [g for g, _ in top]
    counts = [c for _, c in top]

    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    ax.barh(genes[::-1], counts[::-1], color="#55A868", alpha=0.85, edgecolor="black", linewidth=0.5)
    ax.set_xlabel("Citation graph papers retrieved")
    ax.set_title("Citation Graph Contribution by Gene")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig4_citation_graph.png", dpi=300)
    fig.savefig(FIG_DIR / "fig4_citation_graph.pdf")
    plt.close(fig)
    print("✓ fig4_citation_graph.png")


if __name__ == "__main__":
    fig_ablation()
    fig_source_coverage()
    fig_multilingual()
    fig_citation_graph()
    print(f"\nAll figures saved to {FIG_DIR}")