# Benchmark Framework for lit-acquisition

Evaluation framework for the JCDL full paper: "Multi-source multilingual
biomedical literature acquisition with citation graph augmentation".

## Workflow

```
1. Define variants          →  gold_standard/variants.jsonl
2. Collect results          →  python benchmarks/run_benchmark.py --collect
3. Compute metrics + stats  →  python benchmarks/run_benchmark.py --compute
                               (runs make_paper_data.py)
4. External baselines       →  python benchmarks/collect_external_baselines.py
5. Figures                  →  python benchmarks/figures.py
```

## Collection Protocol (corrected)

- **Uniform candidate budget**: every method gets the same `--limit`
  (default 50) so fixed-K metrics are comparable. (The 2026-08-12 runs
  predate this fix: realized budgets were 14.2/20/19.3/26 per variant, and
  the paper reports fixed-K metrics only within budget.)
- **Strictly nested M4**: the citation-graph phase extends the FROZEN M3
  candidate list (seeded by its first DOI) and appends deduplicated
  results. The keyword phase is never re-executed for M4, so component
  attribution is not confounded by live-API run-to-run variance and
  `Recall@K(M4) >= Recall@K(M3)` holds per variant by construction.
- **Provenance**: translated queries (`translated_queries.json`) and a
  machine-readable run manifest (`run_manifest.json`: config echo, model
  identifiers, budgets, per-variant timings) are persisted with each run.
- The LLM relevance gate is disabled during collection (raw retrieval
  comparison); the gate/download layers are not evaluated by this
  benchmark.

## Methods Compared

| Method | Description |
|--------|-------------|
| `pubmed_only` (M1) | PubMed esearch baseline (English only) |
| `multisource_no_translation` (M2) | federated providers, original query |
| `multisource_with_translation` (M3) | M2 + LLM query translation (6 languages) |
| `full_pipeline` (M4) | frozen M3 list + citation graph extension (nested) |
| `full_pipeline_nested` | derived offline for the 2026-08-12 data (see below) |

For the recorded 2026-08-12 collection, `full_pipeline` was an independent
re-run (non-nested); `analysis/nested.py` derives the protocol-correct
nested variant from the raw lists, and the non-nested run is kept solely to
estimate run-to-run API variance.

## Results files (`results/`)

| File | Content |
|------|---------|
| `collected_results.json` | Raw ranked candidate lists per method per variant |
| `derived_results.json` | Strictly nested M4 lists (derived offline) |
| `metrics.json` | Per-variant metrics at K∈{5,10,15,20,50} + set-level |
| `paper_data.json` | Aggregates, Holm-corrected tests, bootstrap CIs, run variance, per-language analysis, fusion baselines, integrity stats, provider failures, run manifest |
| `external_baselines.json` | Direct EuropePMC/OpenAlex/Crossref/S2 single-source runs |
| `translated_queries.json` | Per-variant per-language queries (future runs) |
| `run_manifest.json` | Machine-readable collection manifest (future runs) |
| `timing.json` | Wall-clock acquisition times (2026-08-12 runs) |
| `collect_20260812.log` | Raw stdout of the 2026-08-12 `--collect` run |
| `summary.md` | Human-readable metrics summary |

`make_paper_data.py` deterministically regenerates `derived_results.json`,
`metrics.json`, `paper_data.json`, and `summary.md` from the raw lists —
every number in the paper comes from this script.

## Metrics

- **Recall@K**: fraction of reference-relevant papers in the top-K results
- **Precision@K**: relevant in top-K / **K** (standard fixed denominator;
  methods returning fewer than K candidates are penalized)
- **Precision@Retrieved**: relevant / actual list length (lenient variant,
  reported separately — never labeled "Precision@K")
- **SetRecall**: recall of the entire returned candidate set
- **F1@K**, **nDCG@K**: from the fixed-denominator definitions
- Macro (mean ± std over variants) and micro (pooled counts) aggregates
- Paper identity: normalized DOI > PMID > normalized title

## Statistics

Paired two-sided Wilcoxon signed-rank tests (zeros dropped, exact when
tie-free), **Holm-corrected** as one declared family; matched-pairs
rank-biserial effect sizes; bootstrap 95% CIs of paired deltas (10,000
variant-level resamples, seed 42). See `analysis/stats.py`.

## Caveats

The reference set is heuristic and expert-unverified, with pooling bias
toward the evaluated system's own channels — see
`gold_standard/README.md`. All results are relative coverage comparisons.
