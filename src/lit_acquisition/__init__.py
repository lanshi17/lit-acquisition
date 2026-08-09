"""lit-acquisition: Multilingual biomedical literature acquisition toolkit.

Search, download, and classify academic papers from 15+ providers including
Crossref, PubMed, OpenAlex, EuropePMC, DOAJ, J-STAGE, arXiv, and more.

Quick start::

    from lit_acquisition import configure, search_provider, download_file_from_url

    # Configure your LLM provider (for query translation and relevance gate)
    configure(
        llm_base_url="https://api.openai.com/v1",
        llm_api_key="sk-...",
        llm_model="gpt-4o",
    )

    # Search a single provider
    result = await search_provider(provider="crossref", query="MECP2 Rett syndrome")

    # Or run the full multilingual pipeline
    from lit_acquisition import multilingual_acquisition_workflow
    result = await multilingual_acquisition_workflow({"query": "MECP2 Rett syndrome", "action": "search"})
"""

from .config import (
    LitAcquisitionConfig,
    LLMConfig,
    NetworkConfig,
    PubMedConfig,
    TranslationLLMConfig,
    WebSearchConfig,
    configure,
    get_config,
    reload_config,
)
from .contracts import (
    DownloadResult,
    OnlineAcquisitionGatewayRequest,
    OnlineAcquisitionGatewayResult,
    OnlineAcquisitionItem,
    OnlineAcquisitionRequest,
    OnlineAcquisitionResponse,
    OnlineAcquisitionRouteInfo,
    OnlineAcquisitionSourceTraceEntry,
)
from .gateway import (
    call_provider,
    call_provider_with_retry,
    download_file_from_url,
    resolve_oa_url,
    search_provider,
)
from .literature_type_classifier import (
    LiteratureType,
    classify_item,
    classify_items,
    filter_by_type,
)
from .normalizers import normalize_items
from .provider_health import ProviderHealthTracker, ProviderStats, get_health_tracker
from .pubmed_service import (
    OnlineAcquisitionPubMedArticle,
    OnlineAcquisitionPubMedCandidate,
    OnlineAcquisitionPubMedService,
    get_pubmed_service,
)
from .query_translator import (
    TARGET_LANGUAGES,
    TranslatedQueries,
    translate_query,
)
from .relevance_gate import (
    RelevanceGateResult,
    RelevanceJudgment,
    run_relevance_gate,
)
from .search_service import (
    build_provider_plan,
    dedupe_candidates,
    rank_candidates,
    search_multilingual,
    search_parallel,
)
from .workflow import (
    multilingual_acquisition_workflow,
    online_acquisition_workflow,
)

__all__ = [
    "TARGET_LANGUAGES",
    "DownloadResult",
    "LLMConfig",
    # Config
    "LitAcquisitionConfig",
    # Literature type classifier
    "LiteratureType",
    "NetworkConfig",
    "OnlineAcquisitionGatewayRequest",
    "OnlineAcquisitionGatewayResult",
    # Contracts
    "OnlineAcquisitionItem",
    "OnlineAcquisitionPubMedArticle",
    "OnlineAcquisitionPubMedCandidate",
    # PubMed
    "OnlineAcquisitionPubMedService",
    "OnlineAcquisitionRequest",
    "OnlineAcquisitionResponse",
    "OnlineAcquisitionRouteInfo",
    "OnlineAcquisitionSourceTraceEntry",
    "ProviderHealthTracker",
    "ProviderStats",
    "PubMedConfig",
    # Relevance gate
    "RelevanceGateResult",
    "RelevanceJudgment",
    # Query translator
    "TranslatedQueries",
    "TranslationLLMConfig",
    "WebSearchConfig",
    # Search
    "build_provider_plan",
    # Gateway
    "call_provider",
    "call_provider_with_retry",
    "classify_item",
    "classify_items",
    "configure",
    "dedupe_candidates",
    "download_file_from_url",
    "filter_by_type",
    "get_config",
    # Provider health
    "get_health_tracker",
    "get_pubmed_service",
    "multilingual_acquisition_workflow",
    # Normalizers
    "normalize_items",
    # Workflow
    "online_acquisition_workflow",
    "rank_candidates",
    "reload_config",
    "resolve_oa_url",
    "run_relevance_gate",
    "search_multilingual",
    "search_parallel",
    "search_provider",
    "translate_query",
]

__version__ = "0.1.0"
