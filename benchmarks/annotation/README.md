# E2b — double annotation of the reference set (plan §2.3)

Sampling seed `20261014`; 15 variants x 15 papers = 225 items.
Built by `benchmarks/annotation/build_e2b_sample.py` (re-running reproduces the same sample).

Variants: V003, V006, V012, V015, V017, V019, V020, V022, V025, V027, V029, V032, V036, V037, V042

Papers per disease stratum: {'cardiac': 30, 'hereditary_cancer': 45, 'metabolic': 45, 'neurological': 45, 'other': 15, 'sensory_other': 45}

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
