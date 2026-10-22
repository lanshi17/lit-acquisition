# Bioinformatics Applications Note — submission package

Working manuscript and supplementary material for the Applications Note
described in [`../BIOINFORMATICS_PLAN.md`](../BIOINFORMATICS_PLAN.md).

## Contents

| File | What it is |
|---|---|
| `main.tex` | Manuscript source. **3 pages**: 2 pages of text + 1 page holding Figure 1 and the references. |
| `main.pdf` | Compiled manuscript (also the artifact used for the page-limit check). |
| `supplementary.tex` / `supplementary.pdf` | Supplementary material: reference-set caveats, protocol, full metrics, significance family, SOTA detail, language recovery, provider failures, cost measurements, reproducibility, usage examples, plus the nine review-hardening sections (pooled reference-set expansion, M2--M3 deconfounding, language backfill, ClinVar-curated truth, dual-track unbound-pool SOTA, reranking defect and gating, citation-seed sensitivity, relevance-gate measurement, annotation-package status). |
| `figure1.pdf` / `figure1.png` | Figure 1, three panels, generated from the benchmark artifacts. |
| `make_figure1.py` | Regenerates Figure 1 (reads `benchmarks/results/paper_data.json`). |
| `make_supplementary_tables.py` | Regenerates `tables/*.tex` from `paper_data.json`. |
| `make_review_hardening.py` | Generates `tables/supp_hardening.tex` from the hardening artifacts (plan `EXPERIMENT_OPTIMIZATION_PLAN.md`); every number is read from `benchmarks/results/*.json`. |
| `EXPERIMENT_OPTIMIZATION_PLAN.md` | The reviewer-defence plan (E1--E4c) and its execution status, artifacts and findings. |
| `check_numbers.py` | Recomputes every headline number from the artifacts and asserts it appears in the manuscript or the supplement (70 checks). |
| `tables/` | Generated LaTeX tables included by `supplementary.tex`. **Do not edit by hand.** |

## Build

```bash
cd paper/appnote
latexmk -pdf main.tex            # manuscript
latexmk -pdf supplementary.tex   # supplement
```

Regenerate derived material after any benchmark change:

```bash
python paper/appnote/make_review_hardening.py
python paper/appnote/make_figure1.py
python paper/appnote/make_supplementary_tables.py
python paper/appnote/check_numbers.py     # must print "N/N checks passed"
```

## Review-hardening experiments

The reviewer-defence plan in `EXPERIMENT_OPTIMIZATION_PLAN.md` is executed by
scripts under `benchmarks/analysis/` and `benchmarks/gold_standard/`; all of them
are offline-first and re-runnable from frozen artifacts:

```bash
python benchmarks/analysis/expanded_reference.py       # pooled reference-set expansion
python benchmarks/analysis/deconfound_m2.py            # E1: translation x ranking factorial
python benchmarks/analysis/backfill_languages.py --cross-check   # E4a: language backfill
python benchmarks/gold_standard/build_clinvar_ground_truth.py    # E2a: ClinVar truth (network)
python benchmarks/analysis/clinvar_eval.py             # E2a: offline evaluation
python benchmarks/analysis/sota_fair_pool.py           # E3: dual-track SOTA (uses score cache)
python benchmarks/analysis/rerank_eval_corrected.py    # E4c: corrected M5/M5g
python benchmarks/analysis/citation_seeds.py           # E4c: seed sensitivity (network, cached)
python benchmarks/analysis/gate_eval.py                # E4b: relevance-gate measurement (LLM)
python benchmarks/annotation/build_e2b_sample.py       # E2b: annotation package
```

API results (cross-encoder scores, bge-m3 embeddings, ClinVar responses, citation
traversals, abstracts) are cached under `benchmarks/results/` so re-runs need no
network access beyond cache misses.

## Number provenance

No number in the manuscript is typed from memory. Each one is read from
`benchmarks/results/{paper_data.json,timing_43variants.json,download_throughput.json}`
and the claim-to-artifact map is section 4 of
[`../BIOINFORMATICS_PLAN.md`](../BIOINFORMATICS_PLAN.md). `check_numbers.py`
enforces this: it currently passes 48/48 checks. It has already caught one real
error — an uncorrected $p$-value was labelled as Holm-adjusted ($p=0.35$ vs
$p_{\mathrm{Holm}}=1.00$).

## Template status (read before submitting)

`bioinfo.cls`, the journal's own LaTeX class, is distributed behind the OUP
author-guidelines login and is **not** installed here (it is also not on CTAN;
the generic `oup-authoring-template` is, but it is single-column and does not
reproduce the journal's two-column Applications Note layout). `main.tex`
therefore uses a two-column `article` layout that approximates the published
page geometry, which is what makes the two-page limit verifiable *now*.

At submission:

1. Download the current template from the Bioinformatics author guidelines page.
2. Replace the preamble of `main.tex` (everything from `\documentclass` to the
   `\authornote` definition) with the official class and its options.
3. Keep the body commands (`\section`, `\begin{table}`, `figure*`, the
   `thebibliography` block). Body commands are deliberately generic.
4. Re-check the page limit; the official class sets its own fonts and leading.

## Pending before submission (needs a human)

- [ ] **Author list, affiliations, ORCID iDs, corresponding email** — currently
      a placeholder block. OUP requires ORCID.
- [ ] **Funding statement** and acknowledgements (currently placeholders).
- [ ] **Verify reference metadata** (volume/page numbers) against publisher
      records. Reference *content* is correct; exact pagination was not fetched
      from the publishers.
- [ ] **Expert re-annotation** of the reference set (Cohen's κ) — the single
      biggest review risk; see plan §7.1.
- [ ] **Relevance-gate false-positive rate** — needs human adjudication of a
      sample; the gate was disabled during the benchmark, so this is not
      measurable from the existing artifacts.
- [ ] **User evidence** (3–5 trial users) and GitHub/PyPI adoption figures.
- [ ] Confirm the Applications Note limits (pages, figures, review model, APC)
      against the live author guidelines — the OUP site returned HTTP 403 from
      this machine, so plan §1 is still marked unverified.
- [ ] Decide journal-vs-conference order (plan §5) and, if the double-blind
      conference route is taken, note that the repository is public and the
      preprint carries the author's real name and affiliation.
- [ ] Add `LIT_UNPAYWALL_EMAIL` to the runtime environment, otherwise the
      download layer returns nothing (see `benchmarks/results/download_throughput.json`).
