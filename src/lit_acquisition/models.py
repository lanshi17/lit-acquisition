"""Pure data structures (pydantic models + dataclasses).

This module holds *only* shapes - no behaviour, no I/O, no algorithms.
Request/response contracts, the normalized item, provider-trace entries and
internal gateway types all live here so the rest of the codebase imports its
data model from one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from pydantic import BaseModel, Field, field_validator, model_validator

from .enums import Action, LiteratureType, PreferStrategy, Provider, RouteChoice

# --- Request ---


class OnlineAcquisitionRequest(BaseModel):
    """Unified request for literature search/download."""

    action: Action = Action.SEARCH
    query: str | None = None
    identifiers: list[str] = Field(default_factory=list)
    prefer: PreferStrategy = PreferStrategy.AUTO
    raw: bool = False
    limit: int = 20
    language: str | None = "auto"
    literature_types: list[LiteratureType] = Field(default_factory=list)

    api_provider: Provider | None = None
    api_params: dict[str, Any] = Field(default_factory=dict)

    download_path: str = "./downloads"
    selected_index: int = 0
    selected_title: str | None = None
    detail_link: str | None = None

    # Phase 3 relevance gate
    relevance_gate: bool = Field(
        default=True,
        description="Enable LLM-based relevance filtering after download.",
    )

    timeout: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
        description="Overall phase budget in seconds; slow providers are cut off at this deadline.",
    )
    compact: bool = Field(
        default=False,
        description="Agent-friendly response: omit candidate_links/raw payloads, keep summary+diagnostics.",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        if "identifier" in values and "identifiers" not in values:
            values["identifiers"] = values.get("identifier")
        if "text" in values and "query" not in values:
            values["query"] = values.get("text")
        return values

    @field_validator("identifiers", mode="before")
    @classmethod
    def _identifiers_to_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value if v is not None]
        return [str(value)]

    @field_validator("limit")
    @classmethod
    def _limit_range(cls, value: int) -> int:
        return max(1, min(200, value))


# --- Unified Item ---


class OnlineAcquisitionItem(BaseModel):
    """Standardized literature metadata item."""

    source: str
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    journal: str | None = None
    year: str | None = None
    doi: str | None = None
    url: str | None = None
    links: list[str] = Field(default_factory=list)
    language: str | None = None
    publisher: str | None = None
    issn: list[str] = Field(default_factory=list)
    identifiers: dict[str, Any] = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)
    literature_type: str | None = None
    license: str | None = None


# --- Route Info ---


class OnlineAcquisitionRouteInfo(BaseModel):
    """Routing decision summary."""

    prefer: PreferStrategy
    used: RouteChoice | None = None
    reason: str | None = None
    fallback_used: bool = False


# --- Response ---


class OnlineAcquisitionResponse(BaseModel):
    """Unified response for literature search/download."""

    success: bool
    items: list[OnlineAcquisitionItem] = Field(default_factory=list)
    downloads: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    route: OnlineAcquisitionRouteInfo
    raw: Any | None = None
    candidate_links: list[dict[str, Any]] = Field(
        default_factory=list,
        description="All candidate download links discovered before download phase",
    )
    summary: str = Field(
        default="",
        description="One-line human/agent-readable outcome summary.",
    )
    diagnostics: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured acquisition diagnostics: elapsed_ms, per-provider status/latency/counts.",
    )


# --- Gateway contracts (internal) ---


@dataclass
class OnlineAcquisitionSourceTraceEntry:
    """Single provider call trace for debugging fallback chains."""

    provider: str
    attempt: int
    action: str
    success: bool
    items_count: int
    downloads_count: int
    warnings: list[str]
    error: str | None = None


@dataclass
class OnlineAcquisitionGatewayRequest:
    """Internal request for a single provider call."""

    provider: str
    action: Action = Action.SEARCH
    query: str | None = None
    identifiers: dict[str, str | None] = field(default_factory=dict)
    limit: int = 20
    raw: bool = False
    params: dict[str, Any] = field(default_factory=dict)
    download_path: str = "./downloads"
    selected_index: int = 0
    selected_title: str | None = None
    detail_link: str | None = None
    timeout: float | None = None


@dataclass
class OnlineAcquisitionGatewayResult:
    """Internal result from a single provider call."""

    provider: str
    success: bool
    items: list[dict[str, Any]]
    warnings: list[str]
    downloads: list[dict[str, Any]] = field(default_factory=list)
    raw: Any = None
    meta: Any = None
    source_trace: list[OnlineAcquisitionSourceTraceEntry] = field(default_factory=list)


@dataclass
class DownloadResult:
    """Result of downloading a single file."""

    file_path: str | None = None
    source: str = ""
    doi: str | None = None
    pmcid: str | None = None
    url: str | None = None
    warnings: list[str] = field(default_factory=list)


# --- Provider planning ---


class ProviderPlanItem(TypedDict):
    """A single step in a provider execution plan."""

    route: str
    provider: str
