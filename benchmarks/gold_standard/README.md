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

1. **Variant selection**: 30 variants covering ACMG evidence criteria
   (PS3, PVS1, plus other clinically actionable variants) and different
   disease areas.
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

- `variants.jsonl`: **30 variants** (V001–V030), **540 unique papers**
  (one duplicate annotation — the same DOI recorded twice for V007 with
  different formatting — was removed on 2026-08-13; `total_relevant`
  fields updated accordingly)
- `variants_annotated.jsonl`: same content, pre-filled with relevant papers
- `candidates/`: raw PubMed candidate records used during annotation
- Languages: en 288, de 13, fr 11, unknown 228 (only German and French
  carry non-English annotations)
- **Total annotated papers**: 541 (mean 18.0 per variant, range 4–32)

> **Note**: Earlier documentation referenced 239 papers (79 for V001–V010 +
> 160 for V011–V030). The gold standard has since been re-annotated and
> extended; `variants.jsonl` is the source of truth and currently contains
> 541 relevant papers.

> **Status**: This dataset is a **reference set for relative system
> comparison**, not a clinically verified gold standard. Annotations were
> produced by automated heuristics and have **not** been reviewed by domain
> experts; they may contain false positives and false negatives. Do not use
> absolute recall/precision figures from this dataset as clinical coverage
> estimates without expert re-annotation.

### Disease / evidence-type distribution

| Domain | Count | Representative genes |
|--------|------:|----------------------|
| Cancer genetics | 8 | BRCA1, TP53, PALB2, KRAS, EGFR, BRAF, APC, MLH1 |
| Cardiovascular | 5 | LDLR, SCN5A, MYBPC3, KCNQ1, FBN1 |
| Neurological | 4 | MECP2, HTT, SNCA, APP |
| Metabolic | 3 | CFTR, PAH, GBA |
| Hematological | 4 | HBB, F8, DMD, G6PD |
| Neuromuscular / endocrine / other | 6 | PTEN, MYH7, GJB2, SMN1, CYP21A2, DMPK |

### ACMG evidence criterion coverage

- **PVS1** (null variant in LOF gene): BRCA1, CFTR, PALB2, APC, MLH1, F8, DMD, SMN1, CYP21A2
- **PS3** (well-established functional effect): MECP2, LDLR, TP53, SCN5A, PTEN, MYH7, KRAS, EGFR, BRAF, MYBPC3, KCNQ1, FBN1, HTT, SNCA, APP, PAH, GBA, G6PD, HBB, DMPK

### Annotation notes

- V001–V010 annotated via manual heuristics (gene + disease keyword + pub-type scoring).
- V011–V030 annotated via PubMed E-utilities + automated scoring (gene name / variant token / disease word / pub-type weighted sum).
- Both approaches filter out editorials, letters, comments, and news items.
- **No domain-expert review has been performed.** The dataset is released as a
  reference for comparative evaluation only; use as clinical ground truth
  requires expert re-annotation.

## Next Steps

- [ ] Domain expert verification of `relevant_papers` (especially V011–V030)
- [ ] Expand to 50 variants if needed for statistical power
- [ ] Re-run benchmark with expert-verified annotations
