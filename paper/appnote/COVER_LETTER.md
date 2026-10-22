# Cover Letter for Bioinformatics (Applications Note)

**To:** The Editorial Board, *Bioinformatics* (Oxford University Press)  
**Date:** October 13, 2026  
**Subject:** Submission of Applications Note: "lit-acquisition: multi-source, multilingual biomedical literature acquisition with citation-graph traversal"

Dear Editors,

We are pleased to submit our manuscript entitled **"lit-acquisition: multi-source, multilingual biomedical literature acquisition with citation-graph traversal"** for consideration as an **Applications Note** in *Bioinformatics*.

### Background & Motivation
In clinical genomics and precision medicine, classifying genetic variants under the ACMG/AMP guidelines heavily depends on marshaling published empirical literature—including clinical case reports, functional assays, and family segregation studies. However, the retrieval process remains a critical bottleneck:
1. **Single-database siloing:** Mainstream tools query only PubMed or Europe PMC, missing relevant findings published across non-indexed repositories, preprint servers, and institutional archives.
2. **Language barrier:** Important clinical case reports frequently appear in non-English journals (such as German, French, Japanese, or Russian venues) that standard English-only queries cannot reach.
3. **Overlooked citation links:** Keyword searches miss essential papers whose titles and abstracts omit explicit variant nomenclature, even when they are just one hop away in the citation network.
Commercial curation platforms address subsets of these issues but remain proprietary, expensive, and non-reproducible.

### Key Contributions & Verified Evidence
To address these challenges, we developed **lit-acquisition**, an open-source, modular Python toolkit that unifies 19 literature channels behind a unified asynchronous interface. Key highlights of our work include:
- **Unified 19-Channel Federation:** Integrates 15 pure-Python `httpx` backends (Crossref, OpenAlex, Europe PMC, PMC, DOAJ, J-STAGE, CiNii, SciELO, BASE, CORE, OpenAIRE, arXiv, bioRxiv, medRxiv, Unpaywall) and 4 service clients (PubMed, Semantic Scholar, ClinicalTrials.gov, Zenodo) with connection pooling, health-aware routing, and automated circuit breaking.
- **Cross-Lingual Query Translation & Routing:** Leverages domain-aware LLM translation across six languages (en/zh/ja/de/fr/ru) with strict preservation of HGVS nomenclature and gene symbols.
- **Citation-Graph Traversal:** Implements breadth-first graph traversal over Semantic Scholar edges to discover topologically adjacent but keyword-distant literature.
- **Empirically Verified & Audited Benchmark:** Evaluated on an exploratory reference set of **43 clinically actionable genetic variants (822 papers)**, our end-to-end pipeline raises PubMed-only set recall from **0.269 to 0.467** (+0.198 gain; bootstrap 95% CI [+0.123, +0.269]; Holm-adjusted $p=0.0002$; rank-biserial $r=0.74$). Furthermore, it recovers **21 of 24** annotated German and French papers (88%), whereas every English-only baseline recovers at most one.
- **Two Delivery Editions:** Ships as a lightweight, dependency-minimal API edition (no LLM required) and an advanced LLM-enhanced agent edition.

### Scientific Candor & Integrity
In accordance with rigorous academic reporting, we explicitly disclose the methodological boundaries of our reference set: the candidate pool was heuristically scored and shares overlapping channels with the evaluated pipeline; thus, our findings represent relative retrieval coverage rather than clinical diagnostic completeness. All headline figures in the manuscript have been mathematically cross-checked directly against repository artifacts via automated regression tests (`check_numbers.py`).

### Availability & Community Standards
*lit-acquisition* is published under the permissive MIT license. The software is readily installable via PyPI (`pip install lit-acquisition`, v0.5.0), and all source code, evaluation harnesses, and raw benchmark datasets are publicly hosted on GitHub (https://github.com/lanshi17/lit-acquisition).

This manuscript is original work and is not under consideration elsewhere. All authors have reviewed and approved the submission.

Thank you very much for your time and consideration.

Sincerely,

**[Corresponding Author Name]**  
[Title / Position]  
[Department / Institution]  
[Email Address]  
[ORCID iD]  
