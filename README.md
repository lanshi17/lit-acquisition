# lit-acquisition

Multilingual biomedical literature acquisition toolkit - search, download, and classify academic papers from 18+ providers with citation graph traversal.

## Features

- **18+ provider integrations**: Crossref, PubMed, OpenAlex, EuropePMC, DOAJ, J-STAGE, arXiv, bioRxiv, medRxiv, SciELO, BASE, CORE, OpenAIRE, CiNii, Unpaywall, Semantic Scholar, ClinicalTrials.gov, Zenodo
- **Works out of the box (no native extension required)**: every provider has a pure-Python `httpx` backend. The optional `rust-io` extension is no longer needed to reach providers that lack a dedicated Python service.
- **Citation graph traversal**: Discover related papers by traversing citation networks via Semantic Scholar's API - goes beyond keyword search to find topically related work
- **Multilingual search**: Query translation into 6 languages (en, zh, ja, de, fr, ru) with language-aware provider routing
- **PDF download**: DOI -> Unpaywall OA resolution, PMCID -> EuropePMC render, direct URL with HTML->PDF redirect handling
- **Relevance gate**: LLM-based classification to filter irrelevant downloads
- **Literature type classification**: Keyword-based classification (case report, sequencing, functional study) across 10+ languages
- **Web search fallback**: Firecrawl, Tavily, and SerpApi adapters for discovering papers beyond academic APIs
- **Provider health tracking + circuit breaker**: Sliding-window stats deprioritize unhealthy providers and stop calling ones that are persistently failing
- **Relevance-aware ranking**: Results are ordered by topical match to the query (token-overlap scoring, CJK-bigram aware), with an optional neural rerank stage
- **Fast, bounded fan-out**: shared keep-alive connection pool, per-provider deadlines, and early stopping once enough candidates are gathered - a single slow upstream can no longer stall the whole request
- **Agent-friendly responses**: every workflow returns a one-line `summary` plus structured `diagnostics` (per-provider status/latency/counts, elapsed time), a `compact` mode, and actionable warning codes
- **License awareness**: Each result includes license metadata when available (OA status, CC license, public domain)

## Architecture

The package is organized by functional responsibility, with data, pure logic,
and I/O kept in separate layers:

| Module | Responsibility |
|--------|----------------|
| `enums` | Status/type enumerations (actions, provider status, literature types, warning codes) |
| `models` | Pure data structures (request/response/item, trace entries, gateway types) |
| `algorithms/` | Side-effect-free logic: relevance scoring, ranking, dedup, provider planning, literature-type classification |
| `providers/` | Per-provider search backends (pure-Python `httpx`) and richer service clients |
| `net/` | Connection pool, SSRF/secret security, secure downloads |
| `gateway` | Provider dispatch, per-provider deadlines, error taxonomy, retry policy |
| `orchestration` | Acquisition workflows and concurrent fan-out |
| `normalize` | Per-provider raw-record → `OnlineAcquisitionItem` normalization |
| `llm/` | Query translation, relevance gate, neural rerank |
| `health` | Provider health tracking / circuit breaker |
| `config` | Configuration model and environment loading |

`algorithms/` depends only on `models`/`enums` (no network), so ranking and
classification are unit-testable in isolation; all network access is confined
to `providers/`, `net/`, and `gateway`.

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

Optionally, the Rust native extension can be installed for faster HTTP
I/O on the download path. It is **not required** for any provider - all
18 providers are reachable through the built-in pure-Python backends.

```bash
pip install "lit-acquisition[rust-io]"
```

## Quick Start
### Agent Entry Point

Expose `LITERATURE_SEARCH_TOOL_SCHEMA` to an LLM and dispatch its function call to
`search_literature()`. The result is compact, relevance-ranked, and includes
per-provider diagnostics. An empty result is reported as an operational success
with a `no_results` warning so the agent can broaden the query instead of retrying
the same request.

```python
from lit_acquisition import LITERATURE_SEARCH_TOOL_SCHEMA, search_literature

tools = [LITERATURE_SEARCH_TOOL_SCHEMA]
result = await search_literature("MECP2 Rett syndrome case report", limit=10)
```


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

All 18 providers are reachable through built-in pure-Python backends
(no native extension required). Providers marked with a key require
configuration to be enabled; they are skipped cleanly with an actionable
`CONFIG_MISSING` warning when not configured.

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

Notes on configuration-gated providers:

- **Unpaywall** requires a contact email (`LIT_UNPAYWALL_EMAIL` / `UNPAYWALL_EMAIL`); the API rejects requests without one (HTTP 422). Without it the provider is skipped with a `CONFIG_MISSING` warning instead of failing mid-flight.
- **BASE** and **CORE** need free API keys (`LIT_BASE_API_KEY`, `LIT_CORE_API_KEY`); without a key they are skipped cleanly.
- **bioRxiv / medRxiv** have no public keyword-search API; they are served through EuropePMC's preprint index filtered to the requested server.
- **Semantic Scholar** anonymous access is aggressively rate-limited from shared/data-center IPs; set `LIT_SEMANTIC_SCHOLAR_API_KEY` for reliable throughput.

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
| `LIT_MAX_REDIRECTS` | Max redirects a download may follow | `5` |
| `LIT_MAX_DOWNLOAD_BYTES` | Max bytes read for a single download | `209715200` (200 MB) |
| `LIT_PUBMED_API_KEY` | PubMed eutils API key | - |
| `LIT_SEMANTIC_SCHOLAR_API_KEY` | Semantic Scholar API key (optional, higher rate limits) | - |
| `LIT_SEMANTIC_SCHOLAR_BASE_URL` | Semantic Scholar API base URL | `https://api.semanticscholar.org/graph/v1` |
| `LIT_CLINICAL_TRIALS_BASE_URL` | ClinicalTrials.gov API base URL | `https://clinicaltrials.gov/api/v2` |
| `LIT_ZENODO_BASE_URL` | Zenodo API base URL | `https://zenodo.org/api` |
| `LIT_EUROPEPMC_BASE_URL` | EuropePMC REST search base URL (EBI mirror) | `https://www.ebi.ac.uk/europepmc/webservices/rest` |
| `LIT_UNPAYWALL_EMAIL` / `UNPAYWALL_EMAIL` | Contact email required by the Unpaywall API | - |
| `LIT_BASE_API_KEY` / `BASE_API_KEY` | BASE (Bielefeld) API key | - |
| `LIT_CORE_API_KEY` / `CORE_API_KEY` | CORE API key | - |
| `LIT_PROVIDER_TIMEOUT` | Per-provider search deadline in seconds | `30` |
| `LIT_DOWNLOAD_TIMEOUT` | Per-PDF download deadline in seconds | `60` |

## Agent / Tool Integration

Every acquisition workflow (`online_acquisition_workflow`,
`multilingual_acquisition_workflow`) returns a dict with two fields that
make it easy for an LLM agent to consume and reason about the outcome:

- `summary` (str) — one line, e.g.
  `ok: 24 items in 2.1s (6 providers); top: crossref(9), openalex(6); failed: base`.
- `diagnostics` (dict) — structured detail: `elapsed_ms`, `items_total`,
  `downloads_total`, and a `providers` list where each entry has
  `provider`, `status` (`ok`/`failed`/`skipped`), `attempts`, `items`,
  `warnings`, and `error`.

Warnings are coded so agents can branch on them:

- `CONFIG_MISSING:<provider>:...` — set the named config; retrying will not help.
- `PROVIDER_UNAVAILABLE:<provider>:...` — no usable backend for this action.
- `TIMEOUT:<provider>:...` / `NETWORK_ERROR:<provider>:...` — transient; retry may help.
- `PROVIDER_HTTP_429:<provider>:...` — rate limited; an API key hint is included where applicable.
- Other `PROVIDER_HTTP_<status>:<provider>:...` — non-retryable client errors.

Pass `compact: true` in the request to drop the bulky `candidate_links`
and `raw` payloads (useful when an agent only needs `items` + `summary`).
Pass `timeout` (seconds, 5-300) to cap the overall phase budget.

## Security & Privacy

Network hardening applied to the download path:

- **SSRF protection** — every download candidate *and every hop of a
  redirect chain* is validated against private/reserved address space
  before a connection is made (loopback, RFC1918, link-local, CGN,
  unspecified, IPv4-mapped IPv6, and any other non-global range).
  Non-`http(s)` schemes are rejected.
- **Redirect cap** — downloads follow at most `LIT_MAX_REDIRECTS` (default
  5) redirects, preventing redirect loops/bounces.
- **Download size cap** — response bodies are streamed and truncated at
  `LIT_MAX_DOWNLOAD_BYTES` (default 200 MB), bounding memory use against a
  hostile or broken server.
- **TLS verification is always on** — certificate verification is never
  disabled, for both the Python client and the `curl` fallback.

Privacy:

- **Secrets are redacted** from logs, warnings, and the agent-visible
  response: `api_key`/`token`/`email` query parameters (PubMed, Unpaywall)
  and proxy userinfo credentials (`scheme://user:pass@host`) are masked as
  `REDACTED`.
- **Data sent to third parties** — search queries are necessarily sent to
  the providers you query. If you configure `LIT_UNPAYWALL_EMAIL`, that
  contact email is also sent to Crossref/OpenAlex (as a polite-pool
  `mailto`) and Unpaywall (as the required `email` parameter). Leave it
  unset if you do not want it shared (Unpaywall will then be skipped).

Residual risk (documented): SSRF validation resolves DNS at check time
while the HTTP client resolves again at connect time, so a determined
attacker with a DNS-rebinding server could race the two lookups. Per-hop
validation still blocks the common static-redirect and direct-internal
cases.

## License

MIT
