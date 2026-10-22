"""E2b: build the stratified double-annotation package for the reference set.

Plan reference: ``paper/appnote/EXPERIMENT_OPTIMIZATION_PLAN.md`` section 2.3 (E2b).

The 822-paper reference set was labelled by the project's own title heuristic.
This script prepares the human validation of that heuristic: a stratified sample
of 15 variants x 15 papers (10 heuristic positives, 5 heuristic negatives = 225
papers), two blank annotation sheets, and a key file that is *not* given to the
annotators.

Sampling is deterministic (seeded) and documented:

* variants are stratified by disease area and picked round-robin, so the sample
  spans the reference set instead of the largest disease groups;
* positives come from the frozen ``variants.jsonl`` (papers the heuristic kept);
* negatives come from the pooled candidate lists of all ten systems, restricted
  to papers the documented rule scores at or below the threshold --- these are
  the papers the heuristic *rejected* even though some system retrieved them,
  which is exactly where a false-negative rate would show up.

Outputs (``benchmarks/annotation/``)::

    e2b_sample.csv                 paper metadata + stratum (no labels)
    e2b_sheet_raterA.csv           blank sheet (topic_relevant, variant_level_evidence, notes)
    e2b_sheet_raterB.csv           identical blank sheet for the second rater
    e2b_key.csv                    heuristic labels + scores (analyst only)
    README.md                      protocol, decision rules, agreement workflow

Usage::

    python benchmarks/annotation/build_e2b_sample.py
"""

from __future__ import annotations

import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent / "src"))

from analysis.pooled_gold import candidate_pool  # noqa: E402
from analysis.nested import build_nested_full_pipeline  # noqa: E402
from gold_standard.build_variants import DISEASE_KEYWORDS, _score_title  # noqa: E402
from metrics import paper_id  # noqa: E402

OUT_DIR = BENCH_DIR / "annotation"
RESULTS_DIR = BENCH_DIR / "results"
COLLECTED_FILE = RESULTS_DIR / "collected_results.json"
GOLD_FILE = BENCH_DIR / "gold_standard" / "variants.jsonl"

SEED = 20261014
N_VARIANTS = 15
N_POSITIVES = 10
N_NEGATIVES = 5

# Disease strata: variants are grouped by the disease family of their gene so a
# stratified sample does not over-represent one clinical area.
STRATA = {
    "hereditary_cancer": {"BRCA1", "BRCA2", "TP53", "PALB2", "PTEN", "APC", "MLH1", "CHEK2", "ATM", "CDH1",
                          "RET", "VHL"},
    "cardiac": {"MYH7", "MYBPC3", "KCNQ1", "SCN5A", "FBN1", "RYR1"},
    "metabolic": {"PAH", "G6PD", "GBA", "LDLR", "HFE", "MTHFR", "CYP21A2", "TTR"},
    "neurological": {"MECP2", "HTT", "SNCA", "APP", "DMPK", "SMN1", "DMD", "F8"},
    "sensory_other": {"GJB2", "SLC26A4", "CFTR", "KRAS", "EGFR", "BRAF", "FGFR3", "JAK2"},
}


def stratum_of(gene: str) -> str:
    for name, genes in STRATA.items():
        if gene in genes:
            return name
    return "other"


def load_variants() -> dict[str, dict[str, Any]]:
    return {
        json.loads(line)["variant_id"]: json.loads(line)
        for line in GOLD_FILE.read_text().splitlines()
        if line.strip()
    }


def main() -> None:
    rng = random.Random(SEED)
    variants = load_variants()
    collected = json.loads(COLLECTED_FILE.read_text())
    nested, _ = build_nested_full_pipeline(collected)
    pools = {**collected, "full_pipeline_nested": nested}

    # ── stratified variant selection (round-robin over disease areas) ─────
    by_stratum: dict[str, list[str]] = defaultdict(list)
    for vid, variant in variants.items():
        by_stratum[stratum_of(variant["gene"])].append(vid)
    for vids in by_stratum.values():
        rng.shuffle(vids)
    selected: list[str] = []
    strata_order = sorted(by_stratum, key=lambda name: (-len(by_stratum[name]), name))
    while len(selected) < N_VARIANTS:
        progressed = False
        for name in strata_order:
            if by_stratum[name] and len(selected) < N_VARIANTS:
                selected.append(by_stratum[name].pop())
                progressed = True
        if not progressed:
            break
    selected.sort()

    # ── sample per variant ────────────────────────────────────────────────
    rows: list[dict[str, Any]] = []
    key_rows: list[dict[str, Any]] = []
    for vid in selected:
        variant = variants[vid]
        keywords = DISEASE_KEYWORDS.get(variant["gene"], [])

        positives = list(variant.get("relevant_papers") or [])
        rng.shuffle(positives)
        positives = positives[:N_POSITIVES]

        pool = candidate_pool(pools, vid)
        existing = {paper_id(p) for p in variant.get("relevant_papers") or []}
        negatives = []
        for pid, paper in pool.items():
            if pid in existing:
                continue
            score, reason = _score_title(paper.get("title") or "", variant["gene"], variant["variant"],
                                         keywords, [])
            if score <= 4:
                negatives.append((score, reason, paper))
        rng.shuffle(negatives)
        negatives = negatives[:N_NEGATIVES]

        for label, paper, score, reason in (
            [("heuristic_positive", p, 5, "gene+variant") for p in positives]
            + [("heuristic_negative", p, s, r) for s, r, p in negatives]
        ):
            pid = paper_id(paper) or ""
            rows.append(
                {
                    "sample_id": f"{vid}-{len([r for r in rows if r['variant_id'] == vid]) + 1:02d}",
                    "variant_id": vid,
                    "gene": variant["gene"],
                    "variant": variant["variant"],
                    "disease": variant["disease"],
                    "stratum": stratum_of(variant["gene"]),
                    "stratum_label": label,
                    "title": (paper.get("title") or "").strip(),
                    "journal": (paper.get("journal") or "").strip(),
                    "year": str(paper.get("year") or paper.get("pub_date") or "")[:10],
                    "doi": (paper.get("doi") or "").strip(),
                    "pmid": str(paper.get("pmid") or "").strip(),
                }
            )
            key_rows.append(
                {
                    "sample_id": rows[-1]["sample_id"],
                    "stratum_label": label,
                    "heuristic_score": score,
                    "heuristic_reason": reason,
                }
            )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sample_fields = ["sample_id", "variant_id", "gene", "variant", "disease", "stratum", "stratum_label",
                     "title", "journal", "year", "doi", "pmid"]
    with (OUT_DIR / "e2b_sample.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sample_fields)
        writer.writeheader()
        writer.writerows(rows)

    blank_fields = ["sample_id", "variant_id", "gene", "variant", "title", "journal", "year",
                    "topic_relevant", "variant_level_evidence", "confidence", "notes"]
    for rater in ("A", "B"):
        with (OUT_DIR / f"e2b_sheet_rater{rater}.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=blank_fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({**{k: row[k] for k in ("sample_id", "variant_id", "gene", "variant",
                                                        "title", "journal", "year")},
                                 "topic_relevant": "", "variant_level_evidence": "", "confidence": "",
                                 "notes": ""})

    with (OUT_DIR / "e2b_key.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample_id", "stratum_label", "heuristic_score",
                                                    "heuristic_reason"])
        writer.writeheader()
        writer.writerows(key_rows)

    # ── README with the protocol ──────────────────────────────────────────
    stratum_counts = defaultdict(int)
    for row in rows:
        stratum_counts[row["stratum"]] += 1
    readme = f"""# E2b — double annotation of the reference set (plan §2.3)

Sampling seed `{SEED}`; {len(selected)} variants x {N_POSITIVES + N_NEGATIVES} papers = {len(rows)} items.
Built by `benchmarks/annotation/build_e2b_sample.py` (re-running reproduces the same sample).

Variants: {", ".join(selected)}

Papers per disease stratum: {dict(sorted(stratum_counts.items()))}

## Files

| File | Purpose |
|---|---|
| `e2b_sample.csv` | the sample with metadata only (labels hidden) |
| `e2b_sheet_raterA.csv`, `e2b_sheet_raterB.csv` | identical blank sheets, one per rater |
| `e2b_key.csv` | heuristic labels and scores — **analyst only, do not share with raters** |

## Task (per paper, from the title and abstract)

Fill two binary columns and a confidence column:

1. `topic_relevant` — **Level 1**: does the paper study the target gene *and* the
   target disease/phenotype? (1 = yes, 0 = no)
2. `variant_level_evidence` — **Level 2**: does the title/abstract explicitly
   report *this specific HGVS variant* (case report, family segregation,
   functional assay, cohort frequency for that variant)? (1 = yes, 0 = no)
3. `confidence` — `high` / `medium` / `low`
4. `notes` — one short phrase for anything doubtful

Rules of thumb:

* A paper about the gene and disease but a *different* variant is Level 1 = 1,
  Level 2 = 0.
* A paper that only mentions the variant in passing (e.g. a review listing many
  variants) is Level 1 = 1, Level 2 = 0.
* Gene-panel or guideline papers with no variant-specific content are
  Level 1 = 0, Level 2 = 0.
* Work independently; do not discuss items before both sheets are complete.

## Analysis

```
python benchmarks/analysis/e2b_agreement.py            # after both sheets are filled
```

It reports Cohen's kappa for both levels (target kappa >= 0.70), the heuristic's
precision/recall against each rater and against the consensus, and the estimated
false-negative rate. Items where the two raters disagree are listed for
adjudication; adjudicated values can be added as a third sheet
(`e2b_sheet_adjudicated.csv`, same columns) and passed with `--adjudicated`.
"""
    (OUT_DIR / "README.md").write_text(readme)

    print(f"variants: {len(selected)} ({', '.join(selected)})")
    print(f"papers: {len(rows)}  positives: {sum(1 for r in rows if r['stratum_label'] == 'heuristic_positive')}"
          f"  negatives: {sum(1 for r in rows if r['stratum_label'] == 'heuristic_negative')}")
    print(f"strata: {dict(sorted(stratum_counts.items()))}")
    print(f"wrote {OUT_DIR}/e2b_sample.csv, e2b_sheet_raterA.csv, e2b_sheet_raterB.csv, e2b_key.csv, README.md")


if __name__ == "__main__":
    main()
