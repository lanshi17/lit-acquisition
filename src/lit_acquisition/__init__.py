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

from .citation_graph import (
    TraversalDirection,
    traverse_citation_graph,
)
from .clinical_trials import (
    ClinicalTrialsService,
    get_clinical_trials_service,
)
from .config import (
    ClinicalTrialsConfig,
    LitAcquisitionConfig,
    LLMConfig,
    NetworkConfig,
    PubMedConfig,
    SemanticScholarConfig,
    TranslationLLMConfig,
    WebSearchConfig,
    ZenodoConfig,
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
from .semantic_scholar import (
    SemanticScholarService,
    get_semantic_scholar_service,
)
from .workflow import (
    multilingual_acquisition_workflow,
    online_acquisition_workflow,
)
from .zenodo import (
    ZenodoService,
    get_zenodo_service,
)

__all__ = [
    "TARGET_LANGUAGES",
    "ClinicalTrialsConfig",
    "ClinicalTrialsService",
    "DownloadResult",
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
    "ProviderHealthTracker",
    "ProviderStats",
    "PubMedConfig",
    "RelevanceGateResult",
    "RelevanceJudgment",
    "SemanticScholarConfig",
    "SemanticScholarService",
    "TranslatedQueries",
    "TranslationLLMConfig",
    "TraversalDirection",
    "WebSearchConfig",
    "ZenodoConfig",
    "ZenodoService",
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
    "get_zenodo_service",
    "multilingual_acquisition_workflow",
    "normalize_items",
    "online_acquisition_workflow",
    "rank_candidates",
    "reload_config",
    "resolve_oa_url",
    "run_relevance_gate",
    "search_multilingual",
    "search_parallel",
    "search_provider",
    "translate_query",
    "traverse_citation_graph",
]

__version__ = "0.2.0"
