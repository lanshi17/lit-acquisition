"""Build Figure 1 (three compact panels) for the Bioinformatics Applications Note.

Panel A: the three-phase pipeline as a horizontal stage chain (wide, short).
Panel B: relevant papers by acquisition channel (nested M4 run, first-seen
         attribution after dedup).
Panel C: set recall / Recall@20 for M1-M4n.

All numbers are read from ``benchmarks/results/paper_data.json``; nothing is
hard-coded except labels. Usage::

    python paper/appnote/make_figure1.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PAPER_DATA = ROOT / "benchmarks" / "results" / "paper_data.json"
OUT = Path(__file__).resolve().parent / "figure1"

METHODS = [
    ("pubmed_only", "M1"),
    ("multisource_no_translation", "M2"),
    ("multisource_with_translation", "M3"),
    ("full_pipeline_nested", "M4n"),
]
METHOD_TITLES = "M1 PubMed-only · M2 multi-source · M3 +translation · M4n +citation"
CHANNEL_LABELS = {
    "crossref": "Crossref",
    "pmc": "PMC",
    "semantic_scholar": "Sem. Scholar",
    "europepmc": "EuropePMC",
    "citation_graph": "Citation graph",
    "clinical_trials": "ClinicalTrials",
}

INK = "#1a1a1a"
BLUE = "#2b6cb0"
LIGHT = "#8fb6dd"
ORANGE = "#c05621"
STAGES = [
    ("Search", "19 channels"),
    ("Translate", "6 languages"),
    ("Download", "OA-aware"),
    ("Gate / rank", "rerank + LLM"),
]


def _panel_a(ax: plt.Axes) -> None:
    ax.set_axis_off()
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3.0)

    box_w, gap = 2.02, 0.42
    left = (10 - (len(STAGES) * box_w + (len(STAGES) - 1) * gap)) / 2
    for index, (title, body) in enumerate(STAGES):
        x = left + index * (box_w + gap)
        ax.add_patch(
            FancyBboxPatch(
                (x, 1.35),
                box_w,
                1.15,
                boxstyle="round,pad=0.04,rounding_size=0.12",
                linewidth=0.9,
                edgecolor=BLUE,
                facecolor="#eef4fb",
            )
        )
        ax.text(x + box_w / 2, 2.22, f"{index + 1} · {title}", ha="center", va="center", fontsize=7.4, fontweight="bold", color=BLUE)
        ax.text(x + box_w / 2, 1.72, body, ha="center", va="center", fontsize=6.8, color=INK)
        if index < len(STAGES) - 1:
            ax.add_patch(
                FancyArrowPatch(
                    (x + box_w + 0.04, 1.92),
                    (x + box_w + gap - 0.04, 1.92),
                    arrowstyle="-|>",
                    mutation_scale=8,
                    linewidth=0.9,
                    color=INK,
                )
            )

    ax.text(
        5.0,
        0.72,
        "citation graph traversal: Semantic Scholar edges, depth 1, 20 papers  ·  "
        "two editions: lite (API-only) / agent (LLM)",
        ha="center",
        va="center",
        fontsize=6.6,
        color=ORANGE,
    )
    ax.text(0.02, 2.92, "A", fontsize=10.5, fontweight="bold", va="top", ha="left")


def _panel_b(ax: plt.Axes, data: dict) -> None:
    coverage = data["source_coverage_nested"]
    items = sorted(
        ((CHANNEL_LABELS.get(name, name), info["relevant"]) for name, info in coverage.items()),
        key=lambda pair: pair[1],
    )
    labels = [label for label, _ in items]
    values = [value for _, value in items]
    colors = [ORANGE if label == "Citation graph" else BLUE for label in labels]
    ax.barh(labels, values, color=colors, height=0.6)
    for index, value in enumerate(values):
        ax.text(value + 5, index, str(value), va="center", fontsize=6.6, color=INK)
    ax.set_xlim(0, max(values) * 1.22)
    ax.set_xlabel("relevant papers", fontsize=7.0)
    ax.tick_params(axis="both", labelsize=6.8)
    ax.set_title("B   Channel contribution (394 relevant)", fontsize=7.6, loc="left", color=INK, pad=4)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.grid(axis="x", linewidth=0.4, alpha=0.35)
    ax.set_axisbelow(True)


def _panel_c(ax: plt.Axes, data: dict) -> None:
    aggregates = data["aggregates"]
    labels = [label for _, label in METHODS]
    set_recall = [aggregates[key]["set_recall"]["mean"] for key, _ in METHODS]
    recall_20 = [aggregates[key]["recall_at"]["20"]["mean"] for key, _ in METHODS]
    positions = list(range(len(METHODS)))
    width = 0.36
    bars_a = ax.bar([p - width / 2 for p in positions], set_recall, width, label="SetRecall", color=BLUE)
    bars_b = ax.bar([p + width / 2 for p in positions], recall_20, width, label="Recall@20", color=LIGHT)
    for bars in (bars_a, bars_b):
        for rect in bars:
            ax.text(
                rect.get_x() + rect.get_width() / 2,
                rect.get_height() + 0.01,
                f"{rect.get_height():.3f}",
                ha="center",
                fontsize=5.9,
                color=INK,
            )
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, fontsize=7.0)
    ax.set_ylim(0, 0.63)
    ax.set_ylabel("macro mean, 43 variants", fontsize=7.0)
    ax.tick_params(axis="y", labelsize=6.8)
    ax.legend(fontsize=6.5, frameon=False, loc="upper center", ncol=2, handlelength=1.2, columnspacing=1.0)
    delta = data["tests_holm"]["M4n_vs_M1_set_recall"]
    ax.set_title(f"C   Coverage (M4n−M1 = {delta['delta']:+.3f})", fontsize=7.6, loc="left", color=INK, pad=4)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.grid(axis="y", linewidth=0.4, alpha=0.35)
    ax.set_axisbelow(True)


def main() -> int:
    data = json.loads(PAPER_DATA.read_text(encoding="utf-8"))
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans"],
            "font.size": 7.5,
            "axes.linewidth": 0.6,
            "pdf.fonttype": 42,
        }
    )
    figure = plt.figure(figsize=(7.0, 3.15))
    grid = figure.add_gridspec(
        2,
        2,
        height_ratios=[1.0, 1.35],
        width_ratios=[1.0, 1.0],
        hspace=0.55,
        wspace=0.42,
        left=0.115,
        right=0.985,
        top=0.955,
        bottom=0.105,
    )
    _panel_a(figure.add_subplot(grid[0, :]))
    _panel_b(figure.add_subplot(grid[1, 0]), data)
    _panel_c(figure.add_subplot(grid[1, 1]), data)

    for suffix in ("pdf", "png"):
        figure.savefig(OUT.with_suffix(f".{suffix}"), dpi=300)
    print(f"wrote {OUT.with_suffix('.pdf').relative_to(ROOT)} and {OUT.with_suffix('.png').relative_to(ROOT)}")
    print("caption note:", METHOD_TITLES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
