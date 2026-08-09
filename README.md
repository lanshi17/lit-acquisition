# lit-acquisition

Multilingual biomedical literature acquisition toolkit — search, download, and classify academic papers from 15+ providers.

## Features

- **15+ provider integrations**: Crossref, PubMed, OpenAlex, EuropePMC, DOAJ, J-STAGE, arXiv, bioRxiv, medRxiv, SciELO, BASE, CORE, OpenAIRE, CiNii, Unpaywall
- **Multilingual search**: Query translation into 6 languages (en, zh, ja, de, fr, ru) with language-aware provider routing
- **PDF download**: DOI → Unpaywall OA resolution, PMCID → EuropePMC render, direct URL with HTML→PDF redirect handling
- **Relevance gate**: LLM-based classification to filter irrelevant downloads
- **Literature type classification**: Keyword-based classification (case report, sequencing, functional study) across 10+ languages
- **Web search fallback**: Firecrawl, Tavily, and SerpApi adapters for discovering papers beyond academic APIs
- **Provider health tracking**: Automatic health monitoring with sliding-window stats and unhealthy provider deprioritization

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
)
```

Or via environment variables:

```bash
export LIT_LLM_BASE_URL=https://api.openai.com/v1
export LIT_LLM_API_KEY=sk-...
export LIT_LLM_MODEL=gpt-4o
```

### Search a Single Provider

```python
import asyncio
from lit_acquisition import search_provider

async def main():
    result = await search_provider(
        provider="crossref",
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

## Supported Providers

| Provider | Search | Download | Notes |
|----------|--------|----------|-------|
| Crossref | ✓ | — | Metadata only |
| Unpaywall | ✓ | ✓ | OA PDF resolution via DOI |
| OpenAlex | ✓ | — | Metadata only |
| EuropePMC | ✓ | ✓ | Full text via PMCID render |
| PMC | ✓ | ✓ | esearch + esummary |
| DOAJ | ✓ | — | Metadata only |
| J-STAGE | ✓ | — | Japanese literature |
| CiNii | ✓ | — | Japanese research |
| arXiv | ✓ | ✓ | Preprint server |
| bioRxiv | ✓ | ✓ | Preprint server |
| medRxiv | ✓ | ✓ | Preprint server |
| SciELO | ✓ | — | Latin American literature |
| BASE | ✓ | — | Multidisciplinary |
| CORE | ✓ | — | Open access |
| OpenAIRE | ✓ | — | European research |

## Configuration Reference

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LIT_LLM_BASE_URL` | LLM API base URL | — |
| `LIT_LLM_API_KEY` | LLM API key | — |
| `LIT_LLM_MODEL` | LLM model name | — |
| `LIT_LLM_API_KEYS` | Comma-separated API key pool | — |
| `LIT_LLM_MAX_TOKENS` | Max tokens for LLM | `8192` |
| `LIT_TRANSLATION_BASE_URL` | Translation LLM base URL | Falls back to LLM config |
| `LIT_TRANSLATION_API_KEY` | Translation LLM API key | Falls back to LLM config |
| `LIT_TRANSLATION_MODEL` | Translation LLM model | Falls back to LLM config |
| `LIT_FIRECRAWL_API_KEY` | Firecrawl API key | — |
| `LIT_TAVILY_API_KEY` | Tavily API key | — |
| `LIT_SERPAPI_API_KEY` | SerpApi API key | — |
| `LIT_PROXY` | HTTP/HTTPS/SOCKS proxy URL | — |
| `LIT_NO_PROXY` | Comma-separated proxy bypass domains | `cn,ncbi.nlm.nih.gov,...` |
| `LIT_PUBMED_API_KEY` | PubMed eutils API key | — |

## License

MIT
