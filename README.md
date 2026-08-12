# lit-acquisition

Multilingual biomedical literature acquisition toolkit - search, download, and classify academic papers from 18+ providers with citation graph traversal.

## Paper

A JCDL full-paper draft describing the system and its evaluation is in
[`paper/main.tex`](paper/main.tex) (compiled: `paper/main.pdf`). The
benchmark framework, gold standard (30 variants, 541 annotated papers), and
figure generation scripts live in [`benchmarks/`](benchmarks/).

## Features

- **18+ provider integrations**: Crossref, PubMed, OpenAlex, EuropePMC, DOAJ, J-STAGE, arXiv, bioRxiv, medRxiv, SciELO, BASE, CORE, OpenAIRE, CiNii, Unpaywall, Semantic Scholar, ClinicalTrials.gov, Zenodo
- **Citation graph traversal**: Discover related papers by traversing citation networks via Semantic Scholar's API - goes beyond keyword search to find topically related work
- **Multilingual search**: Query translation into 6 languages (en, zh, ja, de, fr, ru) with language-aware provider routing
- **PDF download**: DOI -> Unpaywall OA resolution, PMCID -> EuropePMC render, direct URL with HTML->PDF redirect handling
- **Relevance gate**: LLM-based classification to filter irrelevant downloads
- **Literature type classification**: Keyword-based classification (case report, sequencing, functional study) across 10+ languages
- **Web search fallback**: Firecrawl, Tavily, and SerpApi adapters for discovering papers beyond academic APIs
- **Provider health tracking**: Automatic health monitoring with sliding-window stats and unhealthy provider deprioritization
- **License awareness**: Each result includes license metadata when available (OA status, CC license, public domain)

## Copyright & License Notice

This toolkit provides **metadata discovery** and **open-access full-text retrieval** only. It does not bypass paywalls, scrape copyrighted content, or circumvent publisher access controls.

- **Metadata** (titles, authors, DOIs, citation data) is factual information and not subject to copyright restrictions under most jurisdictions.
- **Full-text PDFs** are only downloaded from open-access sources (Unpaywall OA resolution, EuropePMC PMC open access, DOAJ, Zenodo open records, Semantic Scholar `openAccessPdf` links).
- **ClinicalTrials.gov** data is U.S. government public domain.
- **Zenodo** metadata is CC0; individual records carry their own licenses.
- **Semantic Scholar** provides metadata and links; it does not host copyrighted PDFs.

Users are responsible for ensuring their use of retrieved content complies with applicable copyright law and publisher terms of service.

## Installation

```bash
pip install lit-acquisition
```

With web search support:

```bash
pip install "lit-acquisition[web-search]"
```

With Rust native extensions (faster HTTP I/O):

```bash
pip install "lit-acquisition[rust-io]"
```

## Quick Start

### Configure

```python
from lit_acquisition import configure

configure(
    # LLM for relevance gate and query translation
    llm_base_url="https://api.openai.com/v1",
    llm_api_key="sk-...",
    llm_model="gpt-4o",

    # Optional: dedicated translation model
    translation_base_url="https://api.openai.com/v1",
    translation_api_key="sk-...",
    translation_model="gpt-4o-mini",

    # Optional: web search providers
    firecrawl_api_key="fc-...",
    tavily_api_key="tvly-...",

    # Optional: network proxy
    proxy="http://127.0.0.1:7890",

    # Optional: PubMed API key (higher rate limits)
    pubmed_api_key="...",

    # Optional: Semantic Scholar API key (higher rate limits)
    semantic_scholar_api_key="...",
)
```

Or via environment variables:

```bash
export LIT_LLM_BASE_URL=https://api.openai.com/v1
export LIT_LLM_API_KEY=sk-...
export LIT_LLM_MODEL=gpt-4o
export LIT_SEMANTIC_SCHOLAR_API_KEY=...  # optional
```

### Search a Single Provider

```python
import asyncio
from lit_acquisition import search_provider

async def main():
    result = await search_provider(
        provider="semantic_scholar",
        query="MECP2 Rett syndrome case report",
        limit=20,
    )
    print(f"Found {len(result.items)} items")
    for item in result.items:
        print(f"  - {item.get('title', 'untitled')}")

asyncio.run(main())
```

### Run the Full Multilingual Pipeline

```python
import asyncio
from lit_acquisition import multilingual_acquisition_workflow

async def main():
    result = await multilingual_acquisition_workflow({
        "query": "MECP2 Rett syndrome case report",
        "action": "search",          # or "download" to also fetch PDFs
        "limit": 30,
        "language": "auto",
        "relevance_gate": True,       # LLM-based relevance filtering
        "literature_types": ["case_report"],
    })
    print(f"Success: {result['success']}")
    print(f"Items: {len(result['items'])}")
    print(f"Downloads: {len(result['downloads'])}")

asyncio.run(main())
```

### Traverse Citation Graph

```python
import asyncio
from lit_acquisition import traverse_citation_graph

async def main():
    # Start from a DOI, find papers that cite or are cited by it
    papers = await traverse_citation_graph(
        seed="10.1038/ng.1234",   # DOI of seed paper
        max_depth=1,               # 1-hop (direct citations/references)
        max_papers=50,
        direction="both",          # "citations", "references", or "both"
    )
    print(f"Found {len(papers)} related papers")
    for p in papers[:5]:
        print(f"  - {p.get('title')} (cited by {p.get('citationCount', 0)})")

asyncio.run(main())
```

### Download PDFs

```python
import asyncio
from lit_acquisition import download_file_from_url

async def main():
    file_path, final_url, warnings = await download_file_from_url(
        url="https://example.com/paper.pdf",
        download_path="./downloads",
        filename_stem="my_paper",
    )
    print(f"Downloaded to: {file_path}")

asyncio.run(main())
```

### Use the PubMed Service

```python
import asyncio
from lit_acquisition import get_pubmed_service

async def main():
    svc = get_pubmed_service()
    candidates = await svc.search_candidates("BRCA1 breast cancer", candidate_limit=10)
    for c in candidates:
        print(f"  PMID: {c.pmid}, Title: {c.title}")

asyncio.run(main())
```

### Use the Semantic Scholar Service

```python
import asyncio
from lit_acquisition import get_semantic_scholar_service

async def main():
    svc = get_semantic_scholar_service()
    papers = await svc.search("MECP2 Rett syndrome", limit=20)
    for p in papers:
        doi = (p.get("externalIds") or {}).get("DOI", "")
        print(f"  - {p.get('title')} (DOI: {doi})")

asyncio.run(main())
```

## Supported Providers

| Provider | Search | Download | License | Notes |
|----------|--------|----------|---------|-------|
| Crossref | ✓ | - | Metadata only | DOI registration |
| Unpaywall | ✓ | ✓ | OA PDF only | OA resolution via DOI |
| OpenAlex | ✓ | - | Metadata only | Open catalog |
| EuropePMC | ✓ | ✓ | OA + PMC | Full text via PMCID |
| PMC | ✓ | ✓ | OA (PMC subset) | esearch + esummary |
| DOAJ | ✓ | - | OA journals | Directory of Open Access Journals |
| J-STAGE | ✓ | - | Metadata only | Japanese literature |
| CiNii | ✓ | - | Metadata only | Japanese research |
| arXiv | ✓ | ✓ | arXiv License | Preprint server |
| bioRxiv | ✓ | ✓ | CC-BY/CC0 | Preprint server |
| medRxiv | ✓ | ✓ | CC-BY/CC0 | Preprint server |
| SciELO | ✓ | - | OA | Latin American literature |
| BASE | ✓ | - | Varies | Multidisciplinary |
| CORE | ✓ | - | OA | Open access aggregator |
| OpenAIRE | ✓ | - | OA | European research |
| **Semantic Scholar** | ✓ | ✓ | Metadata + OA links | 200M+ papers, citation graphs, TLDRs |
| **ClinicalTrials.gov** | ✓ | - | Public domain | U.S. government clinical trial data |
| **Zenodo** | ✓ | ✓ | CC0 metadata, varies | CERN open science repository |

## Configuration Reference

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LIT_LLM_BASE_URL` | LLM API base URL | - |
| `LIT_LLM_API_KEY` | LLM API key | - |
| `LIT_LLM_MODEL` | LLM model name | - |
| `LIT_LLM_API_KEYS` | Comma-separated API key pool | - |
| `LIT_LLM_MAX_TOKENS` | Max tokens for LLM | `8192` |
| `LIT_TRANSLATION_BASE_URL` | Translation LLM base URL | Falls back to LLM config |
| `LIT_TRANSLATION_API_KEY` | Translation LLM API key | Falls back to LLM config |
| `LIT_TRANSLATION_MODEL` | Translation LLM model | Falls back to LLM config |
| `LIT_FIRECRAWL_API_KEY` | Firecrawl API key | - |
| `LIT_TAVILY_API_KEY` | Tavily API key | - |
| `LIT_SERPAPI_API_KEY` | SerpApi API key | - |
| `LIT_PROXY` | HTTP/HTTPS/SOCKS proxy URL | - |
| `LIT_NO_PROXY` | Comma-separated proxy bypass domains | `cn,ncbi.nlm.nih.gov,...` |
| `LIT_PUBMED_API_KEY` | PubMed eutils API key | - |
| `LIT_SEMANTIC_SCHOLAR_API_KEY` | Semantic Scholar API key (optional, higher rate limits) | - |
| `LIT_SEMANTIC_SCHOLAR_BASE_URL` | Semantic Scholar API base URL | `https://api.semanticscholar.org/graph/v1` |
| `LIT_CLINICAL_TRIALS_BASE_URL` | ClinicalTrials.gov API base URL | `https://clinicaltrials.gov/api/v2` |
| `LIT_ZENODO_BASE_URL` | Zenodo API base URL | `https://zenodo.org/api` |

## License

MIT
