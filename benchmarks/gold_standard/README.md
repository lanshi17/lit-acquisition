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

- `variants.jsonl`: 10 variants defined (need to expand to 30-50 for paper)
- `relevant_papers`: NOT YET FILLED - requires manual annotation
- Use `python run_benchmark.py --collect` to gather candidate papers,
  then manually annotate relevance.
