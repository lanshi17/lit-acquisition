# Gold Standard Dataset

## Creation Protocol

1. **Variant selection**: Select variants covering different ACMG evidence
   criteria (PVS1, PS3, PM2, etc.) and different disease areas.
2. **Query formulation**: For each variant, create a natural language query
   that a clinical geneticist would use.
3. **Exhaustive manual search**: For each variant, manually search PubMed,
   EuropePMC, Google Scholar, and language-specific databases (CiNii, SciELO)
   to identify ALL relevant literature.
4. **Inclusion criteria**:
   - Directly discusses the variant or gene-disease pair
   - Contains case report, functional study, or sequencing data
   - Published in a peer-reviewed journal or preprint server
5. **Annotation**: For each paper, record DOI, PMID, title, language, and
   evidence type (case_report, functional, sequencing, review).

## File Format

Each line in `variants.jsonl` is a JSON object:

```json
{
  "variant_id": "V001",
  "gene": "MECP2",
  "variant": "c.470C>T (p.Thr158Met)",
  "disease": "Rett syndrome",
  "acmg_criterion": "PS3",
  "query": "MECP2 Rett syndrome case report",
  "evidence_type": "case_report",
  "languages": ["en", "zh", "ja"],
  "relevant_papers": [
    {
      "doi": "10.1038/xxxxx",
      "pmid": "12345678",
      "title": "Paper title",
      "language": "en",
      "evidence_type": "case_report"
    }
  ],
  "total_relevant": 15
}
```

## Current Status

- `variants.jsonl`: **30 variants** (V001–V030), annotated
- `variants_annotated.jsonl`: same content, pre-filled with relevant papers
- `candidates/`: raw PubMed candidate records used during annotation
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
