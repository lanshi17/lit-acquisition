"""Language-aware provider planning (pure routing table + builder)."""

from __future__ import annotations

from collections.abc import Sequence

from ..models import ProviderPlanItem

LANG_PROVIDER_MATRIX: dict[str, list[ProviderPlanItem]] = {
    "zh": [
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "openalex"},
        {"route": "api", "provider": "doaj"},
        {"route": "api", "provider": "pmc"},
    ],
    "ja": [
        {"route": "api", "provider": "jstage"},
        {"route": "api", "provider": "cinii"},
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "doaj"},
        {"route": "api", "provider": "pmc"},
    ],
    "ko": [
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "doaj"},
    ],
    "es": [
        {"route": "api", "provider": "scielo"},
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "unpaywall"},
    ],
    "pt": [
        {"route": "api", "provider": "scielo"},
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "unpaywall"},
    ],
    "en": [
        {"route": "api", "provider": "pmc"},
        {"route": "api", "provider": "europepmc"},
        {"route": "api", "provider": "pubmed"},
        {"route": "api", "provider": "semantic_scholar"},
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "arxiv"},
        {"route": "api", "provider": "biorxiv"},
        {"route": "api", "provider": "medrxiv"},
        {"route": "api", "provider": "openalex"},
        {"route": "api", "provider": "openaire"},
        {"route": "api", "provider": "base"},
        {"route": "api", "provider": "core"},
        {"route": "api", "provider": "clinical_trials"},
        {"route": "api", "provider": "zenodo"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "doaj"},
    ],
    "de": [
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "europepmc"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "openalex"},
        {"route": "api", "provider": "base"},
        {"route": "api", "provider": "doaj"},
    ],
    "fr": [
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "europepmc"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "openalex"},
        {"route": "api", "provider": "doaj"},
        {"route": "api", "provider": "pmc"},
    ],
    "ru": [
        {"route": "api", "provider": "pmc"},
        {"route": "api", "provider": "europepmc"},
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "openalex"},
    ],
    "auto": [
        {"route": "api", "provider": "crossref"},
        {"route": "api", "provider": "semantic_scholar"},
        {"route": "api", "provider": "unpaywall"},
        {"route": "api", "provider": "openalex"},
        {"route": "api", "provider": "europepmc"},
        {"route": "api", "provider": "clinical_trials"},
        {"route": "api", "provider": "doaj"},
        {"route": "api", "provider": "pmc"},
        {"route": "api", "provider": "pubmed"},
        {"route": "api", "provider": "zenodo"},
    ],
}


def build_provider_plan(
    *,
    language: str = "auto",
    provider_hints: Sequence[str] | None = None,
) -> list[ProviderPlanItem]:
    """Build a provider execution plan based on language and hints."""
    normalized_language = (language or "auto").strip().lower() or "auto"
    plan = list(LANG_PROVIDER_MATRIX.get(normalized_language, LANG_PROVIDER_MATRIX["auto"]))
    hints = [str(item).strip().lower() for item in (provider_hints or []) if str(item).strip()]
    if not hints:
        return plan
    hinted = [item for item in plan if item["provider"] in hints]
    remaining = [item for item in plan if item["provider"] not in hints]
    return hinted + remaining
