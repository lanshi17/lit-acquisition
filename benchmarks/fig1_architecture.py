"""Generate Figure 1: system architecture diagram for the JCDL paper.

Three-phase pipeline: Acquisition (parallel multi-source + multilingual)
-> Normalization/Dedup -> Relevance Gate & Download.
Outputs PNG (300 dpi) + PDF to benchmarks/paper_figures/.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

import scienceplots  # noqa: F401  (registers 'science'/'ieee' styles)

plt.style.use(["science", "ieee", "no-latex"])

from pathlib import Path

FIG_DIR = Path("benchmarks/paper_figures")
FIG_DIR.mkdir(parents=True, exist_ok=True)

C_PHASE = "#4C72B0"
C_SOURCE = "#DD8452"
C_INNER = "#55A868"
C_NOTE = "#E8DCC3"
C_EDGE = "#2F2F2F"
C_BG = "#FFFFFF"

fig, ax = plt.subplots(figsize=(10.5, 6.4))
ax.set_xlim(0, 100)
ax.set_ylim(0, 64)
ax.axis("off")
fig.patch.set_facecolor(C_BG)


def box(x, y, w, h, text, fc, fs=9.0, tc="#111111", ec=C_EDGE, lw=1.1, ls="-", rounded=True):
    style = "round,pad=0.4,rounding_size=1.6" if rounded else "square,pad=0.4"
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=style, fc=fc, ec=ec, lw=lw,
                                linestyle=ls, alpha=0.92))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            color=tc, zorder=5)


def arrow(x1, y1, x2, y2, color=C_EDGE, lw=1.6, rad=0.0, ls="-"):
    ax.add_patch(FancyArrowPatch(
        (x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=15,
        lw=lw, color=color, linestyle=ls,
        connectionstyle=f"arc3,rad={rad}", zorder=4,
    ))


def phase_label(x, text):
    ax.text(x, 61, text, ha="center", va="center", fontsize=12,
            fontweight="bold", color=C_PHASE)


# ================= Phase 1: Acquisition =================
phase_label(17, "Phase 1 · Parallel Multi-source Acquisition")

box(2, 48, 14, 8, "Clinician Query\n(variant / disease)", C_INNER, fs=8.5)
box(2, 35, 14, 7, "LLM Query\nTranslator\n(en $\\rightarrow$ 6 languages)", "#C3C7E8", fs=8)
box(2, 20, 14, 9, "Acquisition\nOrchestrator\n(health-aware\nparallel search)", C_INNER, fs=8)

arrow(9, 48, 9, 43.0)
arrow(9, 35, 9, 30.0)

providers = [
    "PubMed", "EuropePMC", "Crossref", "OpenAlex",
    "Semantic Scholar", "arXiv · bioRxiv · medRxiv",
    "CiNii · J-STAGE · SciELO", "DOAJ · CORE · BASE · OpenAIRE",
    "ClinicalTrials.gov · Zenodo",
]
n = len(providers)
y0, gap, h = 49.0, 4.05, 3.15
for i, name in enumerate(providers):
    y = y0 - i * gap
    box(20, y, 27, h, name, C_SOURCE, fs=7.4, rounded=False)
    arrow(16, 24.5, 20, y + h / 2, color="#888888", lw=0.9, rad=0.0)

# routing note
box(48, 50, 26, 7.5, "Language-aware provider routing\n(zh$\\rightarrow$CiNii/J-STAGE $\cdot$ ja$\\rightarrow$CiNii $\cdot$ de$\\rightarrow$BASE $\cdot$ pt$\\rightarrow$SciELO)",
    C_NOTE, fs=7.6, ec="#888888", ls="--")

# ================= Phase 2: Normalization =================
phase_label(61, "Phase 2 · Normalization & Dedup")

box(50, 24, 22, 10, "Normalization\n(unified OnlineAcquisitionItem;\nDOI / URL / title dedup)", C_INNER, fs=8.2)
box(50, 9, 22, 9, "Candidate Ranking\n(title match · DOI presence\n· recency)", "#C3C7E8", fs=8.2)

arrow(61, 24, 61, 19.0)
arrow(47, 29, 50, 29, lw=2.0, color=C_PHASE)          # providers -> normalization
arrow(61, 18, 75, 32, lw=2.0, color=C_PHASE)          # ranking -> relevance gate

# ================= Phase 3: Relevance Gate & Retrieval =================
phase_label(87, "Phase 3 · Relevance Gate & Retrieval")

box(75, 48, 23, 8, "Citation Graph Traversal\n(Semantic Scholar BFS)", C_SOURCE, fs=8.2, ec="#888888", ls="--")
box(75, 32, 23, 9, "LLM Relevance Gate\n(evidence-type filter)", C_INNER, fs=8.5)
box(75, 19, 23, 8, "OA-aware Download\n(Unpaywall · EuropePMC · URL)", "#E8DCC3", fs=8)
box(75, 4, 23, 8, "Evidence-ready Output\n(metadata + OA PDFs)", C_INNER, fs=8.2)

arrow(86.5, 48, 86.5, 42.5, color="#888888", lw=1.2, ls="--")
arrow(86.5, 32, 86.5, 27.0)
arrow(86.5, 19, 86.5, 12.0)

ax.text(50, 0.8,
        "Fig. 1: Three-phase acquisition pipeline. Phase 1 fans a clinician query out across 18+ providers in up to six "
        "languages with health-aware prioritization;\nPhase 2 unifies heterogeneous schemas and deduplicates by DOI $\\rightarrow$ URL "
        "$\\rightarrow$ normalized title; Phase 3 ranks candidates, traverses the citation graph for recall, applies an LLM relevance "
        "gate, and downloads open-access full texts.",
        ha="center", va="bottom", fontsize=8, color="#333333", style="italic")

fig.savefig(FIG_DIR / "fig1_architecture.png", dpi=300, bbox_inches="tight", facecolor=C_BG)
fig.savefig(FIG_DIR / "fig1_architecture.pdf", bbox_inches="tight", facecolor=C_BG)
print("saved fig1_architecture.png / .pdf")
