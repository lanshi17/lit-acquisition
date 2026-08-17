"""Provider backends.

Two families:

* :mod:`backends` — pure-Python ``httpx`` keyword/identifier search backends
  for the federated providers (Crossref, OpenAlex, EuropePMC, ...), registered
  in ``PY_SEARCH_BACKENDS``.
* service modules (:mod:`pubmed`, :mod:`semantic_scholar`,
  :mod:`clinical_trials`, :mod:`zenodo`) — richer API clients with their own
  methods, dispatched by the gateway.

:mod:`errors` holds :class:`ProviderConfigError`, raised when a provider is
missing required configuration (and must not be retried).
"""

from __future__ import annotations

from .backends import (
    PY_SEARCH_BACKENDS,
    PYTHON_SERVICE_PROVIDERS,
    has_python_search,
)
from .clinical_trials import ClinicalTrialsService, get_clinical_trials_service
from .errors import ProviderConfigError
from .pubmed import (
    OnlineAcquisitionPubMedArticle,
    OnlineAcquisitionPubMedCandidate,
    OnlineAcquisitionPubMedService,
    get_pubmed_service,
)
from .semantic_scholar import SemanticScholarService, get_semantic_scholar_service
from .zenodo import ZenodoService, get_zenodo_service

__all__ = [
    "PYTHON_SERVICE_PROVIDERS",
    "PY_SEARCH_BACKENDS",
    "ClinicalTrialsService",
    "OnlineAcquisitionPubMedArticle",
    "OnlineAcquisitionPubMedCandidate",
    "OnlineAcquisitionPubMedService",
    "ProviderConfigError",
    "SemanticScholarService",
    "ZenodoService",
    "get_clinical_trials_service",
    "get_pubmed_service",
    "get_semantic_scholar_service",
    "get_zenodo_service",
    "has_python_search",
]
