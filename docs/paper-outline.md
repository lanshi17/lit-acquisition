# JCDL Full Paper Outline

**Title**: lit-acquisition: A Multi-source Multilingual Biomedical Literature Acquisition System with Citation Graph Augmentation

**Venue**: JCDL (Joint Conference on Digital Libraries) - CCF-B
**Format**: Full paper (8 pages)
**Deadline**: ~January 2027 (TBD)

---

## Paper Structure

### 1. Introduction (~1 page)

**Motivation**: ACMG/AMP variant classification guidelines require
clinicians to gather evidence from biomedical literature across multiple
languages and databases. This process is manual, time-consuming, and prone
to coverage gaps — especially for non-English literature.

**Problem**: Existing tools (PubMed search, Mastermind, Google Scholar)
each cover limited sources, lack multilingual support, and don't provide
automated full-text retrieval.

**Contributions**:
1. A unified acquisition pipeline integrating 18+ heterogeneous data sources
2. LLM-based cross-lingual query translation with language-aware provider routing
3. Citation graph traversal for recall improvement beyond keyword search
4. Open-source toolkit and benchmark dataset for reproducible evaluation

### 2. Related Work (~1 page)

- **Biomedical literature search**: PubMed, EuropePMC, Semantic Scholar
- **Commercial tools**: Mastermind (Genomenon), Covidence, Rayyan
- **Multi-source federated search**: meta-search engines, aggregated databases
- **Citation analysis in IR**: bibliographic coupling, co-citation, PageRank
- **Cross-lingual IR**: machine translation vs. LLM-based translation

### 3. System Design (~2 pages)

#### 3.1 Architecture Overview
- Three-phase pipeline: acquisition → download → relevance gate
- Orchestrated vertical slice architecture diagram (Figure 1)

#### 3.2 Multi-source Search
- 18+ provider integrations (Table 1: providers, APIs, coverage)
- Language-aware provider routing matrix (Table 2)
- Parallel search with health-aware provider prioritization

#### 3.3 Normalization and Deduplication
- Heterogeneous schema → unified OnlineAcquisitionItem
- Deduplication by DOI > URL > normalized title
- Candidate ranking by title match, DOI presence, recency

#### 3.4 Cross-lingual Query Translation
- LLM-based translation into 6 languages
- Domain-specific prompt preserving gene symbols and variant nomenclature
- Per-language provider routing

#### 3.5 Citation Graph Traversal
- BFS from seed paper via Semantic Scholar API
- Citation and reference edge traversal
- Deduplication against keyword search results

#### 3.6 Download and Relevance Gate
- OA-aware download routing (DOI → Unpaywall, PMCID → EuropePMC)
- LLM-based relevance classification with document type filtering

### 4. Evaluation (~3 pages)

#### 4.1 Gold Standard Dataset
- 30-50 genetic variants across ACMG evidence criteria
- Manually annotated relevant papers (DOI, PMID, language, evidence type)
- Multilingual coverage: en, zh, ja, de, fr, ru, es, pt

#### 4.2 Methods Compared (Ablation Table)
| Method | Components |
|--------|-----------|
| M1: PubMed-only | Single source, English |
| M2: Multi-source | 18 providers, English query |
| M3: + Translation | 18 providers + LLM translation |
| M4: + Citation graph | M3 + citation graph traversal |
| M5: + Relevance gate | M4 + LLM relevance filtering |

#### 4.3 Metrics
- Recall@K, Precision@K, F1@K, nDCG@K (K=50)
- Statistical significance: paired Wilcoxon signed-rank test

#### 4.4 Main Results (Table 3)
- Ablation table showing contribution of each component
- Key finding: citation graph improves recall by X% over keyword-only

#### 4.5 Source Coverage Analysis (Figure 2)
- Venn diagram / bar chart of per-source unique contributions
- Which sources are "unique contributors" (papers not found elsewhere)

#### 4.6 Multilingual Contribution (Figure 3)
- Recall improvement from multilingual search
- Per-language breakdown of relevant papers found

#### 4.7 Deduplication Effectiveness
- Duplication rate across sources
- False merge analysis

#### 4.8 Case Study
- 2-3 detailed examples showing the full pipeline
- Papers found by citation graph that keyword search missed

### 5. Discussion (~0.5 page)
- Limitations: API rate limits, gold standard size, LLM dependency
- Future work: embedding-based semantic search, adaptive traversal depth

### 6. Conclusion (~0.25 page)

### References (~0.5 page, ~25-30 references)

---

## Figures and Tables

| ID | Type | Content |
|----|------|---------|
| Figure 1 | Diagram | System architecture (three-phase pipeline) |
| Figure 2 | Bar chart | Source coverage (unique papers per source) |
| Figure 3 | Bar chart | Multilingual recall by language |
| Figure 4 | Case study | Example: papers found by citation graph vs keyword |
| Table 1 | Table | Provider list (18+, with API, coverage, license) |
| Table 2 | Table | Language-provider routing matrix |
| Table 3 | Table | Main results (ablation: Recall, Precision, F1, nDCG) |
| Table 4 | Table | Source overlap matrix |

---

## Action Items

- [ ] Expand gold standard to 30-50 variants (currently 10)
- [ ] Manually annotate relevant papers for each variant
- [ ] Configure LLM API key for benchmark run
- [ ] Run `python benchmarks/run_benchmark.py --all`
- [ ] Generate figures from results
- [ ] Write paper sections
- [ ] Create architecture diagram (Figure 1)
