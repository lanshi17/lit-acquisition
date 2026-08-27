"""lit-acquisition: multilingual biomedical literature acquisition toolkit.

Search, download, and classify academic papers from 18+ providers.

The package is organized by responsibility:

* :mod:`enums` / :mod:`models` — status enumerations and pure data structures.
* :mod:`algorithms` — side-effect-free logic (ranking, dedup, planning,
  classification).
* :mod:`providers` — per-provider search backends (I/O).
* :mod:`net` — connection pool, SSRF/secret security, secure downloads.
* :mod:`gateway` — provider dispatch, deadlines, error taxonomy, retries.
* :mod:`orchestration` — the acquisition workflows and concurrent fan-out.
* :mod:`llm` — query translation, relevance gate, neural rerank.
* :mod:`normalize`, :mod:`health`, :mod:`config` — supporting modules.

Quick start::

    from lit_acquisition import configure, search_provider

    configure(llm_base_url="https://api.openai.com/v1",
              llm_api_key="sk-...", llm_model="gpt-4o")

    result = await search_provider(provider="crossref", query="MECP2 Rett syndrome")
"""

from __future__ import annotations

# --- Agent entry point ---
from .agent import (
    LITERATURE_SEARCH_TOOL_SCHEMA,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    TOOL_SCHEMA,
    search_literature,
)

# --- Pure algorithms ---
from .algorithms import (
    LANG_PROVIDER_MATRIX,
    build_candidate_id,
    build_provider_plan,
    classify_item,
    classify_items,
    dedupe_candidates,
    filter_by_type,
    lexical_relevance,
    normalize_candidate,
    rank_candidates,
)

# --- Citation graph ---
from .citation_graph import traverse_citation_graph

# --- Configuration ---
from .config import (
    AggregatorKeysConfig,
    ClinicalTrialsConfig,
    EuropePMCConfig,
    HttpPoolConfig,
    LitAcquisitionConfig,
    LLMConfig,
    NetworkConfig,
    PubMedConfig,
    SemanticScholarConfig,
    TranslationLLMConfig,
    UnpaywallConfig,
    WebSearchConfig,
    ZenodoConfig,
    configure,
    get_config,
    reload_config,
)

# --- Enumerations ---
from .enums import (
    Action,
    CandidateType,
    DocType,
    LiteratureType,
    PreferStrategy,
    Provider,
    ProviderStatus,
    RouteChoice,
    TraversalDirection,
)

# --- Gateway ---
from .gateway import (
    call_provider,
    call_provider_with_retry,
    resolve_oa_url,
    search_provider,
)

# --- Health tracking ---
from .health import ProviderHealthTracker, ProviderStats, get_health_tracker

# --- LLM features ---
from .llm import (
    TARGET_LANGUAGES,
    RelevanceGateResult,
    RelevanceJudgment,
    TranslatedQueries,
    neural_rerank,
    rerank_enabled,
    run_relevance_gate,
    translate_query,
)

# --- Data structures ---
from .models import (
    DownloadResult,
    OnlineAcquisitionGatewayRequest,
    OnlineAcquisitionGatewayResult,
    OnlineAcquisitionItem,
    OnlineAcquisitionRequest,
    OnlineAcquisitionResponse,
    OnlineAcquisitionRouteInfo,
    OnlineAcquisitionSourceTraceEntry,
    ProviderPlanItem,
)

# --- Network layer ---
from .net import (
    DownloadSecurityError,
    aclose_all,
    download_file_from_url,
    get_shared_client,
    redact_secrets,
    validate_url_safe,
)

# --- Normalization ---
from .normalize import normalize_items

# --- Orchestration ---
from .orchestration import (
    multilingual_acquisition_workflow,
    online_acquisition_workflow,
    search_multilingual,
    search_parallel,
)

# --- Providers ---
from .providers import (
    ClinicalTrialsService,
    OnlineAcquisitionPubMedArticle,
    OnlineAcquisitionPubMedCandidate,
    OnlineAcquisitionPubMedService,
    ProviderConfigError,
    SemanticScholarService,
    ZenodoService,
    get_clinical_trials_service,
    get_pubmed_service,
    get_semantic_scholar_service,
    get_zenodo_service,
)

__all__ = [
    "LANG_PROVIDER_MATRIX",
    "LITERATURE_SEARCH_TOOL_SCHEMA",
    "TARGET_LANGUAGES",
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
    "TOOL_SCHEMA",
    "Action",
    "AggregatorKeysConfig",
    "CandidateType",
    "ClinicalTrialsConfig",
    "ClinicalTrialsService",
    "DocType",
    "DownloadResult",
    "DownloadSecurityError",
    "EuropePMCConfig",
    "HttpPoolConfig",
    "LLMConfig",
    "LitAcquisitionConfig",
    "LiteratureType",
    "NetworkConfig",
    "OnlineAcquisitionGatewayRequest",
    "OnlineAcquisitionGatewayResult",
    "OnlineAcquisitionItem",
    "OnlineAcquisitionPubMedArticle",
    "OnlineAcquisitionPubMedCandidate",
    "OnlineAcquisitionPubMedService",
    "OnlineAcquisitionRequest",
    "OnlineAcquisitionResponse",
    "OnlineAcquisitionRouteInfo",
    "OnlineAcquisitionSourceTraceEntry",
    "PreferStrategy",
    "Provider",
    "ProviderConfigError",
    "ProviderHealthTracker",
    "ProviderPlanItem",
    "ProviderStats",
    "ProviderStatus",
    "PubMedConfig",
    "RelevanceGateResult",
    "RelevanceJudgment",
    "RouteChoice",
    "SemanticScholarConfig",
    "SemanticScholarService",
    "TranslatedQueries",
    "TranslationLLMConfig",
    "TraversalDirection",
    "UnpaywallConfig",
    "WebSearchConfig",
    "ZenodoConfig",
    "ZenodoService",
    "aclose_all",
    "build_candidate_id",
    "build_provider_plan",
    "call_provider",
    "call_provider_with_retry",
    "classify_item",
    "classify_items",
    "configure",
    "dedupe_candidates",
    "download_file_from_url",
    "filter_by_type",
    "get_clinical_trials_service",
    "get_config",
    "get_health_tracker",
    "get_pubmed_service",
    "get_semantic_scholar_service",
    "get_shared_client",
    "get_zenodo_service",
    "lexical_relevance",
    "multilingual_acquisition_workflow",
    "neural_rerank",
    "normalize_candidate",
    "normalize_items",
    "online_acquisition_workflow",
    "rank_candidates",
    "redact_secrets",
    "reload_config",
    "rerank_enabled",
    "resolve_oa_url",
    "run_relevance_gate",
    "search_literature",
    "search_multilingual",
    "search_parallel",
    "search_provider",
    "translate_query",
    "traverse_citation_graph",
    "validate_url_safe",
]

__version__ = "0.3.1"
