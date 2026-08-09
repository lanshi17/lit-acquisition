# Benchmark Framework for lit-acquisition

Evaluation framework for JCDL full paper: "Multi-source multilingual biomedical
literature acquisition with citation graph augmentation".

## Workflow

```
1. Define variants    →  gold_standard/variants.jsonl
2. Collect results    →  python run_benchmark.py --collect
3. Annotate           →  python run_benchmark.py --annotate
4. Compute metrics    →  python run_benchmark.py --compute
5. Generate analysis  →  python run_benchmark.py --analyze
```

## Methods Compared

| Method | Description |
|--------|-------------|
| `pubmed_only` | PubMed esearch baseline (English only) |
| `pubmed_europepmc` | PubMed + EuropePMC (broader English) |
| `multisource_no_translation` | 18 providers, original query (no LLM translation) |
| `multisource_with_translation` | 18 providers + LLM query translation (no citation graph) |
| `full_pipeline` | 18 providers + LLM translation + citation graph + relevance gate |

This produces an ablation table showing the contribution of each component.

## Metrics

- **Recall@K**: Fraction of relevant papers found in top-K results
- **Precision@K**: Fraction of top-K results that are relevant
- **F1@K**: Harmonic mean of precision and recall
- **nDCG@K**: Normalized discounted cumulative gain (ranking quality)
- **Source coverage**: Per-source contribution to relevant papers found
- **Multilingual recall**: Recall broken down by document language
