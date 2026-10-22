# Submission Statements & Suggested Reviewers for Bioinformatics

## 1. Suggested Reviewers (Recommended Experts)

The following candidates represent recognized international experts in biomedical literature mining, clinical variant interpretation, multilingual information retrieval, and computational biology tooling who have no conflict of interest with the authors:

### Category A: Biomedical Information Retrieval & Literature Mining
1. **Dr. Zhiyong Lu**
   - **Affiliation:** National Center for Biotechnology Information (NCBI), National Library of Medicine (NLM), NIH, Bethesda, MD, USA
   - **Email:** luzh@ncbi.nlm.nih.gov
   - **Expertise:** Biomedical text mining, literature search algorithms, PubTator, LitVar, BioASQ benchmark design.
   - **Reason:** Foremost authority in biomedical entity recognition and literature discovery tools; ideal for evaluating search efficacy and PubMed comparisons.

2. **Prof. Sophia Ananiadou**
   - **Affiliation:** Department of Computer Science, University of Manchester & National Centre for Text Mining (NaCTeM), Manchester, UK
   - **Email:** sophia.ananiadou@manchester.ac.uk
   - **Expertise:** Biomedical natural language processing, semantic literature mining, text curation pipelines.
   - **Reason:** Extensive experience leading large-scale biomedical literature mining and extraction infrastructures.

### Category B: Clinical Variant Interpretation & Genomic Evidence Curation
3. **Dr. Heidi L. Rehm**
   - **Affiliation:** Center for Genomic Medicine, Massachusetts General Hospital; Broad Institute of MIT and Harvard, Boston, MA, USA
   - **Email:** hrehm@mgh.harvard.edu
   - **Expertise:** Clinical variant interpretation, ACMG/AMP guideline implementation, ClinGen, ClinVar.
   - **Reason:** Key architect of clinical variant classification standards; can authoritatively judge the clinical utility of multi-source literature retrieval for ACMG evidence curation.

4. **Dr. Steven Harrison**
   - **Affiliation:** Ambry Genetics & ClinGen Sequence Variant Interpretation (SVI) Working Group, Aliso Viejo, CA, USA
   - **Email:** sharrison@ambrygen.com
   - **Expertise:** Clinical variant interpretation, variant curation pipelines, clinical genetics evidence synthesis.
   - **Reason:** Direct practitioner of variant curation workflows; understands the pain point of missing non-English or non-PubMed literature.

### Category C: Multilingual & Cross-Lingual Information Retrieval
5. **Prof. Dr. Martin Krallinger**
   - **Affiliation:** Medical Text Mining Unit, Barcelona Supercomputing Center (BSC), Barcelona, Spain
   - **Email:** martin.krallinger@bsc.es
   - **Expertise:** Multilingual biomedical NLP, health IR, Cantemist, Mesinesp.
   - **Reason:** Pioneer in non-English biomedical literature extraction and cross-lingual text processing.

6. **Dr. Pierre Zweigenbaum**
   - **Affiliation:** Laboratoire Interdisciplinaire des Sciences du Numérique (LISN), CNRS, Université Paris-Saclay, Orsay, France
   - **Email:** pz@lisn.fr
   - **Expertise:** Biomedical natural language processing, medical terminologies, multilingual medical information extraction.
   - **Reason:** Leading European researcher in French/multilingual biomedical terminology and literature indexing.

### Category D: Open-Source Bioinformatics Software Infrastructure
7. **Prof. Dr. Johannes Köster**
   - **Affiliation:** Department of Biology, University of Duisburg-Essen, Essen, Germany
   - **Email:** johannes.koester@uni-due.de
   - **Expertise:** Reproducible computational biology, workflow managers (Snakemake), Bioconda.
   - **Reason:** Expert in open-source software sustainability, packaging standards, and reproducible pipeline benchmarks in Bioinformatics.

---

## 2. Mandatory OUP Declarations & Statements

### 2.1 Data and Software Availability Statement
The source code for **lit-acquisition** is open source under the MIT license and is hosted on GitHub at https://github.com/lanshi17/lit-acquisition. The software package is distributed via the Python Package Index (PyPI) under the package name `lit-acquisition` (current release v0.5.0; installable via `pip install lit-acquisition` or `pip install "lit-acquisition[agent]"`). 

All benchmark datasets (including the 43 genetic variants and 822 annotated reference candidate papers), experimental logs, and data processing scripts necessary to reproduce the reported metrics are deposited in the repository under the `benchmarks/` directory. Deterministic recreation of all paper numbers and figures is supported by running `python benchmarks/make_paper_data.py`, `python paper/appnote/make_figure1.py`, and `python paper/appnote/check_numbers.py`.

### 2.2 Artificial Intelligence & Large Language Model Disclosure
In compliance with Oxford University Press (OUP) guidelines on Generative AI and AI-assisted technologies, the authors disclose that large language models were utilized as programmatic, experimental components within the software pipeline itself:
- **Query Translation:** `Qwen/Qwen2.5-72B-Instruct` (temperature 0.2, max\_tokens 1024, prompt hash recorded in `benchmarks/results/run_manifest.json`) was used to translate clinical genetic queries into Chinese, Japanese, German, French, and Russian with strict prompt constraints preserving gene symbols and HGVS nomenclature.
- **Neural Reranking:** `BAAI/bge-reranker-v2-m3` was used as a cross-encoder scoring stage.
All prompts, model versions, and run parameters are frozen in the open benchmark artifacts for algorithmic reproducibility. LLMs were not used to generate or falsify scientific text or data in this manuscript.

### 2.3 Conflict of Interest Statement
The authors declare no competing financial or non-financial interests.

### 2.4 Author Contributions (CRediT Taxonomy)
- **Conceptualization:** [Author A, Author B]
- **Methodology:** [Author A]
- **Software:** [Author A]
- **Validation:** [Author A, Author B]
- **Formal Analysis:** [Author A]
- **Investigation:** [Author A, Author B]
- **Data Curation:** [Author A]
- **Writing – Original Draft:** [Author A]
- **Writing – Review & Editing:** [Author A, Author B]
- **Funding Acquisition:** [Author B]

### 2.5 Funding Statement
This work was supported by [Grant Number / Funding Agency Name to be inserted prior to final submission].
