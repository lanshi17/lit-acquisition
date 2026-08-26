"""Standalone configuration for lit-acquisition.

Users configure the library either programmatically::

    from lit_acquisition import configure

    configure(
        llm_base_url="https://api.openai.com/v1",
        llm_api_key="sk-...",
        llm_model="gpt-4o",
    )

or via environment variables::

    LIT_LLM_BASE_URL=https://api.openai.com/v1
    LIT_LLM_API_KEY=sk-...
    LIT_LLM_MODEL=gpt-4o

The configuration is a process-wide singleton accessed via
``get_config()``.
"""

from __future__ import annotations

import os

from loguru import logger
from pydantic import BaseModel, Field

# ── Config models ────────────────────────────────────────────────────────


class LLMConfig(BaseModel):
    """OpenAI-compatible LLM configuration.

    Used for relevance gate (Phase 3) and as the default for query
    translation when no dedicated translation model is configured.
    """

    base_url: str = ""
    model: str = ""
    api_key: str = ""
    api_keys: list[str] = Field(default_factory=list)
    max_tokens: int = 8192
    timeout: int = 60
    temperature: float | None = None

    @property
    def all_api_keys(self) -> list[str]:
        """Return all available API keys (deduplicated, preserving order)."""
        seen: set[str] = set()
        keys: list[str] = []
        for k in [*self.api_keys, self.api_key]:
            k = k.strip()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)
        return keys


class TranslationLLMConfig(BaseModel):
    """Dedicated translation model for cross-lingual query translation.

    Falls back to :class:`LLMConfig` when fields are empty.
    """

    base_url: str = ""
    model: str = ""
    api_key: str = ""
    api_keys: list[str] = Field(default_factory=list)
    max_tokens: int = 8192
    timeout: int = 60
    temperature: float | None = 0.2

    @property
    def all_api_keys(self) -> list[str]:
        seen: set[str] = set()
        keys: list[str] = []
        for k in [*self.api_keys, self.api_key]:
            k = k.strip()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)
        return keys


class WebSearchConfig(BaseModel):
    """Web search provider configuration."""

    firecrawl_api_key: str = ""
    base_url: str = "https://api.firecrawl.dev"
    timeout: int = 30
    max_results: int = 10

    tavily_api_key: str = ""
    tavily_search_depth: str = "basic"

    serpapi_api_key: str = ""
    serpapi_engine: str = "google"


class NetworkConfig(BaseModel):
    """Outbound HTTP proxy with per-domain bypass.

    ``proxy`` is the proxy URL for international sites (e.g.
    ``http://127.0.0.1:7890`` or ``socks5://127.0.0.1:7891``).

    ``no_proxy`` is a comma-separated list of domain suffixes that should
    bypass the proxy and connect directly.

    Security bounds for the download path:

    * ``max_redirects`` caps how many HTTP redirects a download may follow
      (each hop is SSRF-validated); this limits redirect-loop / bounce abuse.
    * ``max_download_bytes`` caps how much of a response body is read into
      memory, preventing a malicious or misbehaving server from exhausting
      memory (a denial-of-service vector).
    """

    proxy: str = ""
    no_proxy: str = "cn,ncbi.nlm.nih.gov,nlm.nih.gov,unpaywall.org,localhost,127.0.0.1"
    max_redirects: int = 5
    max_download_bytes: int = 200 * 1024 * 1024  # 200 MB

    def resolve_proxy_for_url(self, url: str) -> str | None:
        """Return the proxy URL to use for *url*, or ``None`` for direct."""
        if not self.proxy:
            return None
        from urllib.parse import urlparse

        hostname = urlparse(url).hostname or ""
        if not hostname:
            return None
        for domain in self.no_proxy.split(","):
            domain = domain.strip().lower()
            if not domain:
                continue
            if hostname == domain or hostname.endswith("." + domain):
                return None
        return self.proxy


class PubMedConfig(BaseModel):
    """PubMed eutils API configuration."""

    base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    api_key: str = ""


class SemanticScholarConfig(BaseModel):
    """Semantic Scholar Graph API configuration.

    The API is free; an API key increases rate limits from ~100 to
    ~5000 requests per 5 minutes.
    """

    base_url: str = "https://api.semanticscholar.org/graph/v1"
    api_key: str = ""


class ClinicalTrialsConfig(BaseModel):
    """ClinicalTrials.gov v2 API configuration.

    U.S. government public domain data - no API key required.
    """

    base_url: str = "https://clinicaltrials.gov/api/v2"


class ZenodoConfig(BaseModel):
    """Zenodo REST API configuration.

    CERN-operated open science repository. Metadata is CC0.
    No API key required for search.
    """

    base_url: str = "https://zenodo.org/api"


class EuropePMCConfig(BaseModel):
    """EuropePMC REST search configuration.

    The JSON search API is served from the EBI mirror
    (``/europepmc/webservices/rest/search``); the ``www.europepmc.org``
    web frontend returns HTML for the same path, so the mirror is the
    default.
    """

    base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest"


class UnpaywallConfig(BaseModel):
    """Unpaywall OA resolution configuration.

    The Unpaywall API **requires** an ``email`` query parameter
    (requests without one are rejected with HTTP 422). When no email
    is configured, the provider is skipped with an actionable
    ``CONFIG_MISSING`` warning instead of burning network calls.
    """

    email: str = ""
    base_url: str = "https://api.unpaywall.org/v2"


class HttpPoolConfig(BaseModel):
    """HTTP connection-pool and timeout tuning for provider calls.

    A shared keep-alive pool avoids a fresh TCP+TLS handshake per
    provider call; per-provider deadlines stop one slow upstream from
    dominating end-to-end latency.

    ``provider_timeout`` is deliberately generous (30s) because several
    legitimate providers (Zenodo, OpenAIRE, J-STAGE) can be slow; a
    tighter value would fail them even though they eventually respond.
    Wall time stays bounded because fan-out is parallel, stops early once
    enough candidates are gathered, and the circuit breaker skips
    providers that fail persistently.
    """

    provider_timeout: float = 30.0
    download_timeout: float = 60.0
    max_connections: int = 64
    max_keepalive_connections: int = 32
    keepalive_expiry: float = 30.0


class AggregatorKeysConfig(BaseModel):
    """API keys for aggregators that require registration.

    BASE (Bielefeld) and CORE both require a free API key; when a key
    is missing the corresponding provider is skipped with a
    ``CONFIG_MISSING`` warning rather than failing at request time.
    """

    base_api_key: str = ""
    core_api_key: str = ""


class LitAcquisitionConfig(BaseModel):
    """Root configuration for lit-acquisition.

    Access nested domains via attributes::

        cfg = get_config()
        cfg.llm.api_key
        cfg.network.proxy
    """

    llm: LLMConfig = Field(default_factory=LLMConfig)
    translation: TranslationLLMConfig = Field(default_factory=TranslationLLMConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    pubmed: PubMedConfig = Field(default_factory=PubMedConfig)
    semantic_scholar: SemanticScholarConfig = Field(default_factory=SemanticScholarConfig)
    clinical_trials: ClinicalTrialsConfig = Field(default_factory=ClinicalTrialsConfig)
    zenodo: ZenodoConfig = Field(default_factory=ZenodoConfig)
    europepmc: EuropePMCConfig = Field(default_factory=EuropePMCConfig)
    unpaywall: UnpaywallConfig = Field(default_factory=UnpaywallConfig)
    http: HttpPoolConfig = Field(default_factory=HttpPoolConfig)
    aggregator_keys: AggregatorKeysConfig = Field(default_factory=AggregatorKeysConfig)


# ── Singleton ────────────────────────────────────────────────────────────

_config: LitAcquisitionConfig | None = None


def get_config() -> LitAcquisitionConfig:
    """Return the process-wide config singleton.

    On first call, loads from environment variables.
    Subsequent calls return the cached instance unless :func:`reload_config`
    is called.
    """
    global _config
    if _config is None:
        _config = _load_from_env()
    return _config


def configure(
    *,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
    llm_api_keys: list[str] | None = None,
    llm_max_tokens: int | None = None,
    translation_base_url: str | None = None,
    translation_model: str | None = None,
    translation_api_key: str | None = None,
    translation_api_keys: list[str] | None = None,
    firecrawl_api_key: str | None = None,
    tavily_api_key: str | None = None,
    serpapi_api_key: str | None = None,
    proxy: str | None = None,
    no_proxy: str | None = None,
    max_redirects: int | None = None,
    max_download_bytes: int | None = None,
    pubmed_api_key: str | None = None,
    pubmed_base_url: str | None = None,
    semantic_scholar_api_key: str | None = None,
    semantic_scholar_base_url: str | None = None,
    clinical_trials_base_url: str | None = None,
    zenodo_base_url: str | None = None,
    europepmc_base_url: str | None = None,
    unpaywall_email: str | None = None,
    provider_timeout: float | None = None,
    download_timeout: float | None = None,
    base_api_key: str | None = None,
    core_api_key: str | None = None,
) -> LitAcquisitionConfig:
    """Configure the library programmatically.

    Only provided arguments are updated; existing values are preserved.

    Example::

        from lit_acquisition import configure

        configure(
            llm_base_url="https://api.openai.com/v1",
            llm_api_key="sk-...",
            llm_model="gpt-4o",
        )
    """
    cfg = get_config()

    if llm_base_url is not None:
        cfg.llm.base_url = llm_base_url
    if llm_model is not None:
        cfg.llm.model = llm_model
    if llm_api_key is not None:
        cfg.llm.api_key = llm_api_key
    if llm_api_keys is not None:
        cfg.llm.api_keys = llm_api_keys
    if llm_max_tokens is not None:
        cfg.llm.max_tokens = llm_max_tokens

    if translation_base_url is not None:
        cfg.translation.base_url = translation_base_url
    if translation_model is not None:
        cfg.translation.model = translation_model
    if translation_api_key is not None:
        cfg.translation.api_key = translation_api_key
    if translation_api_keys is not None:
        cfg.translation.api_keys = translation_api_keys

    if firecrawl_api_key is not None:
        cfg.web_search.firecrawl_api_key = firecrawl_api_key
    if tavily_api_key is not None:
        cfg.web_search.tavily_api_key = tavily_api_key
    if serpapi_api_key is not None:
        cfg.web_search.serpapi_api_key = serpapi_api_key

    if proxy is not None:
        cfg.network.proxy = proxy
    if no_proxy is not None:
        cfg.network.no_proxy = no_proxy
    if max_redirects is not None:
        cfg.network.max_redirects = max(0, min(20, max_redirects))
    if max_download_bytes is not None:
        cfg.network.max_download_bytes = max(1024 * 1024, max_download_bytes)

    if pubmed_api_key is not None:
        cfg.pubmed.api_key = pubmed_api_key
    if pubmed_base_url is not None:
        cfg.pubmed.base_url = pubmed_base_url

    # Semantic Scholar
    if semantic_scholar_api_key is not None:
        cfg.semantic_scholar.api_key = semantic_scholar_api_key
    if semantic_scholar_base_url is not None:
        cfg.semantic_scholar.base_url = semantic_scholar_base_url

    # ClinicalTrials.gov
    if clinical_trials_base_url is not None:
        cfg.clinical_trials.base_url = clinical_trials_base_url

    # Zenodo
    if zenodo_base_url is not None:
        cfg.zenodo.base_url = zenodo_base_url

    # EuropePMC
    if europepmc_base_url is not None:
        cfg.europepmc.base_url = europepmc_base_url

    # Unpaywall (email is mandatory for the API to accept requests)
    if unpaywall_email is not None:
        cfg.unpaywall.email = unpaywall_email

    # HTTP tuning
    if provider_timeout is not None:
        cfg.http.provider_timeout = provider_timeout
    if download_timeout is not None:
        cfg.http.download_timeout = download_timeout

    # Aggregator keys (BASE, CORE)
    if base_api_key is not None:
        cfg.aggregator_keys.base_api_key = base_api_key
    if core_api_key is not None:
        cfg.aggregator_keys.core_api_key = core_api_key

    return cfg


def reload_config() -> LitAcquisitionConfig:
    """Discard the cached config and reload from environment variables."""
    global _config
    _config = _load_from_env()
    return _config


def _load_from_env() -> LitAcquisitionConfig:
    """Build config from environment variables."""
    cfg = LitAcquisitionConfig()

    # LLM
    cfg.llm.base_url = os.getenv("LIT_LLM_BASE_URL", "")
    cfg.llm.model = os.getenv("LIT_LLM_MODEL", "")
    cfg.llm.api_key = os.getenv("LIT_LLM_API_KEY", "")
    keys_str = os.getenv("LIT_LLM_API_KEYS", "")
    if keys_str:
        cfg.llm.api_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
    max_tok = os.getenv("LIT_LLM_MAX_TOKENS")
    if max_tok:
        try:
            cfg.llm.max_tokens = int(max_tok)
        except ValueError:
            logger.warning("invalid LIT_LLM_MAX_TOKENS={!r}, keeping default {}", max_tok, cfg.llm.max_tokens)

    # Translation LLM
    cfg.translation.base_url = os.getenv("LIT_TRANSLATION_BASE_URL", "")
    cfg.translation.model = os.getenv("LIT_TRANSLATION_MODEL", "")
    cfg.translation.api_key = os.getenv("LIT_TRANSLATION_API_KEY", "")
    tkeys_str = os.getenv("LIT_TRANSLATION_API_KEYS", "")
    if tkeys_str:
        cfg.translation.api_keys = [k.strip() for k in tkeys_str.split(",") if k.strip()]

    # Web search
    cfg.web_search.firecrawl_api_key = os.getenv("LIT_FIRECRAWL_API_KEY", os.getenv("WEB_SEARCH_API_KEY", ""))
    cfg.web_search.base_url = os.getenv("LIT_FIRECRAWL_BASE_URL", os.getenv("WEB_SEARCH_BASE_URL", "https://api.firecrawl.dev"))
    cfg.web_search.tavily_api_key = os.getenv("LIT_TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))
    cfg.web_search.tavily_search_depth = os.getenv("LIT_TAVILY_SEARCH_DEPTH", "basic")
    cfg.web_search.serpapi_api_key = os.getenv("LIT_SERPAPI_API_KEY", os.getenv("SERPAPI_API_KEY", ""))
    cfg.web_search.serpapi_engine = os.getenv("LIT_SERPAPI_ENGINE", "google")

    # Network
    cfg.network.proxy = os.getenv("LIT_PROXY", os.getenv("HTTP_PROXY", os.getenv("HTTPS_PROXY", "")))
    cfg.network.no_proxy = os.getenv(
        "LIT_NO_PROXY",
        "cn,ncbi.nlm.nih.gov,nlm.nih.gov,unpaywall.org,localhost,127.0.0.1",
    )
    max_redir = os.getenv("LIT_MAX_REDIRECTS")
    if max_redir:
        try:
            cfg.network.max_redirects = max(0, min(20, int(max_redir)))
        except ValueError:
            logger.warning("invalid LIT_MAX_REDIRECTS={!r}, keeping default {}", max_redir, cfg.network.max_redirects)
    max_dl = os.getenv("LIT_MAX_DOWNLOAD_BYTES")
    if max_dl:
        try:
            cfg.network.max_download_bytes = max(1024 * 1024, int(max_dl))
        except ValueError:
            logger.warning(
                "invalid LIT_MAX_DOWNLOAD_BYTES={!r}, keeping default {}", max_dl, cfg.network.max_download_bytes
            )

    # PubMed
    cfg.pubmed.api_key = os.getenv("LIT_PUBMED_API_KEY", os.getenv("PUBMED_API_KEY", ""))
    cfg.pubmed.base_url = os.getenv("LIT_PUBMED_BASE_URL", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils")

    # Semantic Scholar
    cfg.semantic_scholar.base_url = os.getenv(
        "LIT_SEMANTIC_SCHOLAR_BASE_URL", "https://api.semanticscholar.org/graph/v1"
    )
    cfg.semantic_scholar.api_key = os.getenv("LIT_SEMANTIC_SCHOLAR_API_KEY", "")

    # ClinicalTrials.gov
    cfg.clinical_trials.base_url = os.getenv(
        "LIT_CLINICAL_TRIALS_BASE_URL", "https://clinicaltrials.gov/api/v2"
    )

    # Zenodo
    cfg.zenodo.base_url = os.getenv("LIT_ZENODO_BASE_URL", "https://zenodo.org/api")

    # EuropePMC (EBI REST mirror serves JSON; the web frontend returns HTML)
    cfg.europepmc.base_url = os.getenv(
        "LIT_EUROPEPMC_BASE_URL", "https://www.ebi.ac.uk/europepmc/webservices/rest"
    )

    # Unpaywall (email parameter is required by the API)
    cfg.unpaywall.email = os.getenv("LIT_UNPAYWALL_EMAIL", os.getenv("UNPAYWALL_EMAIL", ""))

    # HTTP tuning
    ptimeout = os.getenv("LIT_PROVIDER_TIMEOUT")
    if ptimeout:
        try:
            cfg.http.provider_timeout = max(1.0, float(ptimeout))
        except ValueError:
            logger.warning("invalid LIT_PROVIDER_TIMEOUT={!r}, keeping default {}", ptimeout, cfg.http.provider_timeout)
    dtimeout = os.getenv("LIT_DOWNLOAD_TIMEOUT")
    if dtimeout:
        try:
            cfg.http.download_timeout = max(5.0, float(dtimeout))
        except ValueError:
            logger.warning("invalid LIT_DOWNLOAD_TIMEOUT={!r}, keeping default {}", dtimeout, cfg.http.download_timeout)

    # Aggregator keys
    cfg.aggregator_keys.base_api_key = os.getenv("LIT_BASE_API_KEY", os.getenv("BASE_API_KEY", ""))
    cfg.aggregator_keys.core_api_key = os.getenv("LIT_CORE_API_KEY", os.getenv("CORE_API_KEY", ""))

    return cfg
