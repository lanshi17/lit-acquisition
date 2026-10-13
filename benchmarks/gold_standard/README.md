# Reference Dataset (NOT an expert-verified gold standard)

## What this dataset is

An **exploratory reference set for relative system comparison**. The
"relevant papers" were selected by an automated heuristic protocol, have
**not** been reviewed by domain experts, and were drawn from a candidate
pool retrieved through channels that overlap the evaluated system's own
providers (a pooling/circularity bias). Absolute recall/precision against
this set measures agreement with the heuristic protocol, not clinical
retrieval quality.

## Actual Creation Protocol

1. **Variant selection**: clinically actionable variants across different
   disease areas. The original 30 (V001–V030) were chosen to cover ACMG
   evidence criteria (PS3, PVS1); 13 further variants (V031–V043) were added
   under the same policy to raise statistical power (2026-08-14).
2. **Query formulation**: For each variant, a natural language query that a
   clinical geneticist would plausibly use.
3. **Candidate pooling (automated)**: candidates retrieved from PubMed
   E-utilities, EuropePMC, Crossref, and language-specific databases.
   *This pooling is NOT exhaustive and overlaps the evaluated system's
   sources.*
4. **Heuristic scoring (automated)**: weighted title/pub-type scoring —
   gene name in title +3; variant identifier +5; disease keywords up to +3;
   preferred pub-types (case reports) +2; reviews −2; editorials, letters,
   comments, and news excluded.
5. **Recording**: DOI, PMID, title, language, and coarse evidence type
   (case_report / functional / sequencing / other).

Known limitations: unquantified false positives (keyword-matching papers
that do not bear on the variant) and false negatives (relevant papers the
pooling never surfaced); no distinction between "about the gene/disease"
and "usable as variant-level ACMG evidence"; language labels come from
provider metadata (many unknown).

## File Format

Each line in `variants.jsonl` is a JSON object:

```json
{
  "variant_id": "V001",
  "gene": "MECP2",
  "variant": "c.470C>T",
  "disease": "Rett syndrome",
  "query": "MECP2 Rett syndrome case report",
  "relevant_papers": [
    {
      "doi": "10.1038/xxxxx",
      "pmid": "12345678",
      "title": "Paper title",
      "language": "en",
      "source": "pubmed",
      "evidence_type": "other",
      "annotation_reason": "gene_and_disease"
    }
  ],
  "total_relevant": 17
}
```

## Current Status

- `variants.jsonl`: **43 variants** (V001–V043), **822 unique papers** — the
  source of truth. Extension from 30 to 43 variants (2026-08-14) reused the
  documented protocol via `build_variants.py`; the original 30 were left
  untouched. One duplicate annotation (the same DOI recorded twice for V007
  with different formatting) was removed on 2026-08-13.
- `variants_annotated.jsonl`: **historical** — covers V001–V030 only
  (540 papers). Kept for provenance; do not use it as the current reference
  set.
- `candidates/`: raw candidate records for V011–V043 (plus an unused V044
  draft whose variant was never added).
- Languages: en 493, de 13, fr 11, unknown 305 (only German and French
  carry non-English annotations)
- **Total annotated papers**: 822 (mean 19.1 per variant, range 4–36)

> **Note**: Earlier documentation referenced 239 papers (79 for V001–V010 +
> 160 for V011–V030), then 541 papers / 30 variants. `variants.jsonl` is the
> source of truth and currently contains **43 variants / 822 papers**; the
> per-paper language labels come from provider metadata and remain unknown
> for 305 of them.

> **Status**: This dataset is a **reference set for relative system
> comparison**, not a clinically verified gold standard. Annotations were
> produced by automated heuristics and have **not** been reviewed by domain
> experts; they may contain false positives and false negatives. Do not use
> absolute recall/precision figures from this dataset as clinical coverage
> estimates without expert re-annotation.

### Disease / evidence-type distribution

All 43 variants, grouped by disease area:

| Domain | Count | Representative genes |
|--------|------:|----------------------|
| Cancer genetics | 13 | BRCA1, BRCA2, TP53, PALB2, CHEK2, ATM, KRAS, EGFR, BRAF, APC, MLH1, RET, VHL, CDH1 |
| Cardiovascular | 5 | LDLR, SCN5A, MYBPC3, KCNQ1, FBN1 |
| Neurological | 4 | MECP2, HTT, SNCA, APP |
| Metabolic | 5 | CFTR, PAH, GBA, HFE, MTHFR |
| Hematological | 5 | HBB, F8, DMD, G6PD, JAK2 |
| Neuromuscular / endocrine / skeletal / other | 11 | PTEN, MYH7, GJB2, SMN1, CYP21A2, DMPK, FGFR3, TTR, SLC26A4, RYR1, ATM |

*(Domain assignment is editorial, one variant per row; ATM appears under
cancer genetics and its locus is also listed with the ataxia group.)*

### ACMG evidence criterion coverage (V001–V030 subset)

Criterion labels below were assigned during construction of the original
30-variant subset. The 13 added variants were selected under the same
"clinically actionable variant" policy (see `build_variants.py`), but no
per-variant criterion label was recorded for them — do not attribute these
lists to V031–V043.

- **PVS1** (null variant in LOF gene): BRCA1, CFTR, PALB2, APC, MLH1, F8, DMD, SMN1, CYP21A2
- **PS3** (well-established functional effect): MECP2, LDLR, TP53, SCN5A, PTEN, MYH7, KRAS, EGFR, BRAF, MYBPC3, KCNQ1, FBN1, HTT, SNCA, APP, PAH, GBA, G6PD, HBB, DMPK

### Annotation notes

- V001–V010 annotated via manual heuristics (gene + disease keyword + pub-type scoring).
- V011–V030 annotated via PubMed E-utilities + automated scoring (gene name / variant token / disease word / pub-type weighted sum).
- V031–V043 annotated by `build_variants.py`, which replicates the same protocol
  (pooling from PubMed E-utilities + EuropePMC + Crossref — channels that
  overlap the evaluated system, so the documented pooling bias is preserved
  by design).
- All approaches filter out editorials, letters, comments, and news items.
- **No domain-expert review has been performed.** The dataset is released as a
  reference for comparative evaluation only; use as clinical ground truth
  requires expert re-annotation.

## Next Steps

- [ ] **Domain expert verification of `relevant_papers`** — highest priority;
      stratified sample (≥30 variants, two annotators, Cohen's κ), separating
      "about the gene/disease" from "usable as variant-level ACMG evidence"
- [x] Expand beyond 30 variants for statistical power (43 variants, 822 papers, 2026-08-14)
- [ ] Re-run the benchmark with expert-verified annotations
- [ ] Publish a frozen snapshot (Zenodo DOI) — `benchmarks/` is excluded from
      the PyPI sdist, so the reference set is only available via the repository
- [ ] Decide whether the unused `candidates/V044_candidates.jsonl` draft should
      become V044 or be deleted
