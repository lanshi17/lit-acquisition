"""Status and type enumerations.

Every categorical value used across the toolkit lives here so callers never
compare against magic strings. All enums are :class:`str` mixins, so they
serialize naturally to JSON and interoperate with plain strings where a
provider API expects one.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """Base for string-valued enums (``str`` mixin for JSON friendliness)."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Action(StrEnum):
    """What an acquisition request should do."""

    SEARCH = "search"
    DOWNLOAD = "download"


class PreferStrategy(StrEnum):
    """Which acquisition route a request prefers."""

    AUTO = "auto"
    API = "api"
    WEB = "web"


class RouteChoice(StrEnum):
    """Which route actually served a request (reported in diagnostics)."""

    API = "api"
    WEB = "web"
    NONE = "none"


class ProviderStatus(StrEnum):
    """Outcome of a single provider call for diagnostics."""

    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


class CandidateType(StrEnum):
    """Where a merged candidate came from."""

    API = "api"
    FIRECRAWL = "firecrawl"


class LiteratureType(StrEnum):
    """Coarse literature categories used for filtering/classification."""

    CASE_REPORT = "case_report"
    SEQUENCING = "sequencing"
    FUNCTIONAL = "functional"


class DocType(StrEnum):
    """Document types emitted by the LLM relevance gate."""

    CASE_REPORT = "case_report"
    SEQUENCING = "sequencing"
    FUNCTIONAL = "functional"
    REVIEW = "review"
    THESIS = "thesis"
    OTHER = "other"


class TraversalDirection(StrEnum):
    """Citation-graph traversal direction."""

    CITATIONS = "citations"
    REFERENCES = "references"
    BOTH = "both"


class Provider(StrEnum):
    """Known literature providers.

    Used for routing plans, health tracking, and normalizer dispatch. A few
    pseudo-providers (``api``, ``web_search``) used only as trace labels are
    intentionally excluded.
    """

    CROSSREF = "crossref"
    UNPAYWALL = "unpaywall"
    OPENALEX = "openalex"
    EUROPEPMC = "europepmc"
    PMC = "pmc"
    PUBMED = "pubmed"
    DOAJ = "doaj"
    JSTAGE = "jstage"
    ARXIV = "arxiv"
    BIORXIV = "biorxiv"
    MEDRXIV = "medrxiv"
    SCIELO = "scielo"
    BASE = "base"
    CORE = "core"
    OPENAIRE = "openaire"
    CINII = "cinii"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    CLINICAL_TRIALS = "clinical_trials"
    ZENODO = "zenodo"


# Literal alias kept for pydantic field validation where a closed string
# set is required.
ApiProvider = str
