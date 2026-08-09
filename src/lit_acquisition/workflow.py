"""Online acquisition workflow — three-phase pipeline.

Phase 1 (Link Acquisition): Parallel search from API providers + Firecrawl.
Phase 2 (Download): Route candidates by type — DOI → OA API, PMCID → EuropePMC render (PMC direct fallback), direct URL → HTTP.
Phase 3 (Gate): LLM classification on downloaded PDF content.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import asdict
from typing import Any

from loguru import logger

from .contracts import (
    DownloadResult,
    OnlineAcquisitionGatewayResult,
    OnlineAcquisitionItem,
    OnlineAcquisitionRequest,
    OnlineAcquisitionResponse,
    OnlineAcquisitionRouteInfo,
    OnlineAcquisitionSourceTraceEntry,
)
from .gateway import (
    _normalize_doi,
    download_file_from_url,
    resolve_oa_url,
    search_provider,
)
from .literature_type_classifier import classify_item
from .normalizers import DOI_PATTERN, normalize_items
from .query_translator import TARGET_LANGUAGES, translate_query
from .relevance_gate import run_relevance_gate
from .search_service import build_provider_plan, dedupe_candidates, rank_candidates, search_parallel
from .web_search import SearchLink, WebSearchResult

PMCID_PATTERN = re.compile(r"\bPMC\d+\b", re.IGNORECASE)
PMID_PATTERN = re.compile(r"PMID[:\s]*([0-9]{5,9})", re.IGNORECASE)

# API providers to search in parallel (order matters for result priority)
_API_SEARCH_PROVIDERS = [
    "crossref",
    "unpaywall",
    "openalex",
    "europepmc",
    "pmc",
    "doaj",
    "jstage",
    "arxiv",
    "biorxiv",
    "medrxiv",
    "scielo",
    "base",
    "core",
    "openaire",
    "cinii",
]

# Identifier-specific provider overrides
_ID_PROVIDER_MAP: dict[str, list[str]] = {
    "doi": ["crossref", "unpaywall", "openalex", "europepmc"],
    "pmid": ["pmc", "europepmc"],
    "pmcid": ["pmc"],
}


def _extract_identifiers(texts: list[str]) -> dict[str, str | None]:
    info: dict[str, str | None] = {"doi": None, "pmcid": None, "pmid": None}
    for text in texts:
        if not text:
            continue
        if not info["doi"]:
            doi_match = DOI_PATTERN.search(text)
            if doi_match:
                info["doi"] = _normalize_doi(doi_match.group(0))
        if not info["pmcid"]:
            pmcid_match = PMCID_PATTERN.search(text)
            if pmcid_match:
                info["pmcid"] = pmcid_match.group(0)
        if not info["pmid"]:
            pmid_match = PMID_PATTERN.search(text)
            if pmid_match:
                info["pmid"] = pmid_match.group(1)
            elif text.isdigit() and 5 <= len(text) <= 9:
                info["pmid"] = text
    return info


def _build_query(request: OnlineAcquisitionRequest) -> str:
    if request.query:
        return request.query.strip()
    return " ".join([s for s in request.identifiers if s])


def _build_gateway_identifiers(identifiers: dict[str, str | None]) -> dict[str, str | None]:
    return {k: v for k, v in identifiers.items() if v is not None}


def _resolve_language(request: OnlineAcquisitionRequest, identifiers: dict[str, str | None]) -> str | None:
    """Resolve language code for download path organization."""
    lang = (request.language or "").strip().lower()
    if lang and lang != "auto":
        return lang
    doi = identifiers.get("doi")
    if doi and (doi.startswith(("10.3760/", "10.3969/"))):
        return "zh"
    return None


# ── Phase 1: Link Acquisition ───────────────────────────────────────────


async def _acquire_links_api(
    *,
    query: str,
    identifiers: dict[str, str | None],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Phase 1a: Search API providers in parallel, return raw items with metadata."""
    doi = identifiers.get("doi")
    pmid = identifiers.get("pmid")
    pmcid = identifiers.get("pmcid")

    if doi:
        providers = _ID_PROVIDER_MAP["doi"]
    elif pmid or pmcid:
        providers = _ID_PROVIDER_MAP.get("pmid" if pmid else "pmcid", ["pmc"])
    else:
        providers = _API_SEARCH_PROVIDERS

    id_params = {k: v for k, v in identifiers.items() if v}

    async def _search_one(provider: str) -> OnlineAcquisitionGatewayResult | None:
        try:
            return await search_provider(
                provider=provider,
                query=query,
                identifiers=id_params,
                limit=limit,
                raw=False,
                params={},
            )
        except Exception as exc:
            logger.debug("api search {} failed: {}", provider, exc)
            return None

    results = await asyncio.gather(*[_search_one(p) for p in providers], return_exceptions=True)

    all_items: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, Exception):
            continue
        if result and result.success:
            for item in result.items:
                if isinstance(item, dict):
                    item["_source_provider"] = result.provider
                    all_items.append(item)

    return all_items


async def _acquire_links_web_search(
    *,
    query: str,
    language: str | None = None,
) -> list[SearchLink]:
    """Phase 1b: Search via all configured web search adapters in parallel.

    Runs Tavily, SerpApi, and Firecrawl concurrently when their API keys are
    configured, then merges and deduplicates results by URL.  Tavily and
    Firecrawl also scrape their top 5 results for additional PDF links.
    """
    from .config import get_config

    cfg = get_config()
    ws = cfg.web_search

    adapter_specs: list[tuple[str, Any]] = []

    if ws.tavily_api_key:
        from .web_search.tavily_adapter import TavilyAdapter

        adapter_specs.append(
            (
                "tavily",
                TavilyAdapter(
                    api_key=ws.tavily_api_key,
                    search_depth=ws.tavily_search_depth,
                    max_results=ws.max_results,
                ),
            )
        )

    if ws.serpapi_api_key:
        from .web_search.serpapi_adapter import SerpApiAdapter

        adapter_specs.append(
            (
                "serpapi",
                SerpApiAdapter(
                    api_key=ws.serpapi_api_key,
                    engine=ws.serpapi_engine,
                    max_results=ws.max_results,
                ),
            )
        )

    if ws.firecrawl_api_key:
        from .web_search.firecrawl_adapter import FirecrawlAdapter

        adapter_specs.append(
            (
                "firecrawl",
                FirecrawlAdapter(
                    api_key=ws.firecrawl_api_key,
                    base_url=ws.base_url,
                    timeout=ws.timeout,
                    max_results=ws.max_results,
                ),
            )
        )

    if not adapter_specs:
        logger.info(
            "web search skipped: no TAVILY_API_KEY, SERPAPI_API_KEY, or WEB_SEARCH_FIRECRAWL_API_KEY configured"
        )
        return []

    async def _search_one(name: str, adapter: Any) -> WebSearchResult:
        try:
            return await adapter.search(query, language=language)
        except Exception as exc:
            logger.warning("{} search failed: {}", name, exc)
            return WebSearchResult(links=[], query=query, provider=name, warnings=[str(exc)])

    results = await asyncio.gather(*[_search_one(n, a) for n, a in adapter_specs])

    all_links: list[SearchLink] = []
    seen_urls: set[str] = set()

    # Collect search results and launch scrape tasks for providers that support it
    scrape_tasks: list[Any] = []
    for (name, adapter), result in zip(adapter_specs, results):
        if result.warnings:
            for w in result.warnings:
                logger.warning("{}: {}", name, w)
        for link in result.links:
            if link.url and link.url not in seen_urls:
                seen_urls.add(link.url)
                all_links.append(link)
        # Tavily and Firecrawl support scrape_links; SerpApi does not
        if name in ("tavily", "firecrawl") and result.links:
            top5 = [link for link in result.links[:5] if link.url]
            scrape_tasks.extend(adapter.scrape_links(link.url) for link in top5)

    if scrape_tasks:
        scrape_results = await asyncio.gather(*scrape_tasks, return_exceptions=True)
        for sr in scrape_results:
            if isinstance(sr, list):
                for link in sr:
                    if link.url and link.url not in seen_urls:
                        seen_urls.add(link.url)
                        all_links.append(link)

    return all_links


def _source_trace_entry(
    *,
    provider: str,
    success: bool,
    items_count: int = 0,
    downloads_count: int = 0,
    warnings: list[str] | None = None,
    error: str | None = None,
) -> OnlineAcquisitionSourceTraceEntry:
    """Build a provider trace entry for acquisition diagnostics."""
    return OnlineAcquisitionSourceTraceEntry(
        provider=provider,
        attempt=1,
        action="search",
        success=success,
        items_count=items_count,
        downloads_count=downloads_count,
        warnings=warnings or [],
        error=error,
    )


def _coerce_str(value: Any) -> str:
    """Extract first usable string from str | list | tuple | None.

    Crossref returns title as a list of strings; some providers return
    nested dicts.  This normalises all shapes to a plain string.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        for item in value:
            s = _coerce_str(item)
            if s:
                return s
        return ""
    if isinstance(value, dict):
        return _coerce_str(value.get("value") or value.get("title") or next(iter(value.values()), ""))
    return ""


def _merge_and_dedupe(
    api_items: list[dict[str, Any]],
    firecrawl_links: list[SearchLink],
) -> list[dict[str, Any]]:
    """Merge API items and Firecrawl links, deduplicate by DOI/URL/title."""
    seen_dois: set[str] = set()
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    merged: list[dict[str, Any]] = []

    def _norm_title(raw: Any) -> str:
        t = _coerce_str(raw)
        if not t:
            return ""
        return re.sub(r"[^\w\s]", "", t.lower()).strip()

    for item in api_items:
        doi = _coerce_str(item.get("doi") or item.get("DOI")).strip().lower()
        url = _coerce_str(item.get("url") or item.get("URL") or item.get("link")).strip()
        title = _norm_title(item.get("title") or item.get("article_title"))

        if doi and doi in seen_dois:
            continue
        if url and url in seen_urls:
            continue
        if title and title in seen_titles:
            continue

        if doi:
            seen_dois.add(doi)
        if url:
            seen_urls.add(url)
        if title:
            seen_titles.add(title)

        item["_candidate_type"] = "api"
        merged.append(item)

    for link in firecrawl_links:
        url = link.url.strip()
        doi = (link.doi or "").strip().lower()

        # Check DOI dedup against API items
        if doi and doi in seen_dois:
            continue
        if url in seen_urls:
            continue

        if doi:
            seen_dois.add(doi)
        seen_urls.add(url)

        merged.append(
            {
                "url": url,
                "title": link.title or "",
                "doi": link.doi or "",
                "_source_provider": link.source or "firecrawl",
                "_candidate_type": "firecrawl",
            }
        )

    return merged


# ── Phase 2: Download ───────────────────────────────────────────────────


async def _download_candidates(
    candidates: list[dict[str, Any]],
    download_path: str,
) -> list[DownloadResult]:
    """Phase 2: Download files from candidate links.

    Routing:
    - DOI → unpaywall OA resolution → download
      (requires ``UNPAYWALL_EMAIL`` env var, set via
      ``backend/config/environments/<env>.yaml`` → ``unpaywall.email``)
    - PMCID → EuropePMC render endpoint → PMC direct PDF URL fallback
    - Direct URL → HTTP download (with HTML→PDF redirect handling)

    The PMC direct URL (``ncbi.nlm.nih.gov/pmc/articles/PMC{x}/pdf/``)
    serves a JavaScript "Preparing to download" interstitial page rather
    than the PDF bytes, so it is used only as a last-resort fallback.
    EuropePMC's render endpoint (``europepmc.org/articles/PMC{x}?pdf=render``)
    streams the actual PDF and is tried first.
    """

    async def _download_one(candidate: dict[str, Any]) -> DownloadResult | None:
        doi = _coerce_str(candidate.get("doi") or candidate.get("DOI")).strip() or None
        pmid = _coerce_str(candidate.get("pmid")).strip() or None
        if not pmid and isinstance(candidate.get("identifiers"), dict):
            pmid = _coerce_str(candidate["identifiers"].get("pmid")).strip() or None
        pmcid = _coerce_str(candidate.get("pmcid")).strip() or None
        url = _coerce_str(candidate.get("url") or candidate.get("URL")).strip() or None
        title = _coerce_str(candidate.get("title")) or "untitled"
        url_for_hash = url or doi or pmcid or title or "unknown"
        url_hash = hashlib.md5(url_for_hash.encode()).hexdigest()[:8]
        filename_stem = f"{re.sub(r'[^\w\-]', '_', title)[:70]}_{url_hash}"

        # Route 1: DOI → unpaywall OA resolution
        if doi:
            try:
                id_params = {"doi": doi}
                result = await search_provider(
                    provider="unpaywall",
                    query="",
                    identifiers=id_params,
                    limit=1,
                    raw=False,
                    params={},
                )
                oa_url = resolve_oa_url(result)
                if oa_url:
                    file_path, final_url, warns = await download_file_from_url(oa_url, download_path, filename_stem)
                    if file_path:
                        return DownloadResult(
                            file_path=file_path,
                            source="unpaywall",
                            doi=doi,
                            url=final_url,
                            warnings=warns,
                        )
            except Exception as exc:
                logger.debug("unpaywall download failed for {}: {}", doi, exc)

        # Route 2: PMCID → EuropePMC render (primary) → PMC direct (fallback)
        if pmcid:
            pmcid_url_candidates = [
                f"https://europepmc.org/articles/{pmcid}?pdf=render",
                f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/",
            ]
            for pdf_url in pmcid_url_candidates:
                file_path, final_url, warns = await download_file_from_url(pdf_url, download_path, filename_stem)
                if file_path:
                    return DownloadResult(
                        file_path=file_path,
                        source="pmc",
                        pmcid=pmcid,
                        url=final_url,
                        warnings=warns,
                    )

        # Route 3: Direct URL download
        if url:
            file_path, final_url, warns = await download_file_from_url(url, download_path, filename_stem)
            if file_path:
                return DownloadResult(
                    file_path=file_path,
                    source=candidate.get("_source_provider", "direct"),
                    url=final_url,
                    warnings=warns,
                )

        return None

    results = await asyncio.gather(*[_download_one(c) for c in candidates], return_exceptions=True)
    downloads: list[DownloadResult] = []
    for result in results:
        if isinstance(result, DownloadResult):
            downloads.append(result)
        elif isinstance(result, Exception):
            logger.warning("candidate download failed: {}", result)

    return downloads


def _apply_type_filter(
    normalized_items: list[OnlineAcquisitionItem],
    candidates: list[dict[str, Any]],
    literature_types: list[str] | None,
) -> tuple[list[OnlineAcquisitionItem], list[dict[str, Any]]]:
    """Filter items and candidates by literature type. Returns (items, candidates)."""
    if not literature_types:
        return normalized_items, candidates
    typed_items = []
    for ni in normalized_items:
        lt = classify_item(ni)
        ni.literature_type = lt.value if lt else None
        if lt and lt.value in literature_types:
            typed_items.append(ni)
    allowed_dois = {(ni.doi or "").strip().lower() for ni in typed_items if ni.doi}
    allowed_titles = {(ni.title or "").strip().lower()[:80] for ni in typed_items if ni.title}
    if allowed_dois or allowed_titles:
        filtered = []
        for c in candidates:
            c_doi = (c.get("doi") or "").strip().lower()
            c_title = (c.get("title") or "").strip().lower()[:80]
            if (c_doi and c_doi in allowed_dois) or (c_title and c_title in allowed_titles):
                filtered.append(c)
        if filtered:
            candidates = filtered
    return typed_items, candidates


# ── Main Entry Point ────────────────────────────────────────────────────


async def online_acquisition_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """Main entry point — three-phase online acquisition pipeline.

    Phase 1: Parallel link acquisition (API providers + Firecrawl).
    Phase 2: Download by candidate type (DOI→OA, PMCID→PMC, URL→direct).
    Phase 3: LLM content gate on downloaded PDFs.
    """
    # --- Validate request ---
    try:
        request = OnlineAcquisitionRequest(**payload)
    except Exception as exc:
        route = OnlineAcquisitionRouteInfo(prefer="auto", used="none", reason="invalid_request")
        return OnlineAcquisitionResponse(
            success=False,
            items=[],
            warnings=[f"invalid_request: {exc}"],
            route=route,
            candidate_links=[],
        ).model_dump()
    query = _build_query(request)
    identifiers = _extract_identifiers([request.query or ""] + request.identifiers)
    language = _resolve_language(request, identifiers)

    route = OnlineAcquisitionRouteInfo(
        prefer=request.prefer,
        api_provider=request.api_provider,
        used="web" if request.prefer == "web" else "api",
        reason="parallel_acquisition",
        fallback_used=False,
    )
    warnings: list[str] = []
    source_trace: list[OnlineAcquisitionSourceTraceEntry] = []

    download_path = request.download_path
    if language:
        download_path = os.path.join(download_path, language)

    # === Phase 1: Link Acquisition (parallel) ===
    id_params = _build_gateway_identifiers(identifiers)

    api_items: list[dict[str, Any]] = []
    firecrawl_links: list[SearchLink] = []

    if request.prefer == "web":
        try:
            firecrawl_links = await _acquire_links_web_search(query=query, language=language)
            source_trace.append(
                _source_trace_entry(
                    provider="web_search",
                    success=bool(firecrawl_links),
                    items_count=len(firecrawl_links),
                )
            )
        except Exception as exc:
            logger.warning("web search acquisition failed: {}", exc)
            warning = f"web search acquisition failed: {exc}"
            warnings.append(warning)
            source_trace.append(
                _source_trace_entry(
                    provider="web_search",
                    success=False,
                    warnings=[warning],
                    error=str(exc),
                )
            )
            firecrawl_links = []
    elif request.prefer == "api":
        try:
            api_items = await _acquire_links_api(query=query, identifiers=id_params, limit=request.limit)
            source_trace.append(
                _source_trace_entry(
                    provider="api",
                    success=bool(api_items),
                    items_count=len(api_items),
                )
            )
        except Exception as exc:
            logger.warning("api acquisition failed: {}", exc)
            warning = f"api acquisition failed: {exc}"
            warnings.append(warning)
            source_trace.append(
                _source_trace_entry(
                    provider="api",
                    success=False,
                    warnings=[warning],
                    error=str(exc),
                )
            )
            api_items = []
    else:
        # Auto: deterministic identifiers (DOI/PMID/PMCID) route API-only —
        # direct provider APIs are authoritative and Firecrawl would only
        # waste credits scraping landing pages that never yield PDFs.
        has_deterministic_id = bool(identifiers.get("doi") or identifiers.get("pmid") or identifiers.get("pmcid"))

        if has_deterministic_id:
            try:
                api_items = await _acquire_links_api(
                    query=query,
                    identifiers=id_params,
                    limit=request.limit,
                )
                source_trace.append(
                    _source_trace_entry(
                        provider="api",
                        success=bool(api_items),
                        items_count=len(api_items),
                    )
                )
            except Exception as exc:
                logger.warning("api acquisition failed: {}", exc)
                warning = f"api acquisition failed: {exc}"
                warnings.append(warning)
                source_trace.append(
                    _source_trace_entry(
                        provider="api",
                        success=False,
                        warnings=[warning],
                        error=str(exc),
                    )
                )
            route = OnlineAcquisitionRouteInfo(
                prefer=request.prefer,
                api_provider=request.api_provider,
                used="api",
                reason="deterministic_identifier",
                fallback_used=False,
            )
        else:
            api_task = _acquire_links_api(query=query, identifiers=id_params, limit=request.limit)
            firecrawl_task = _acquire_links_web_search(query=query, language=language)

            api_result, firecrawl_result = await asyncio.gather(api_task, firecrawl_task, return_exceptions=True)

            if isinstance(api_result, Exception):
                logger.warning("api acquisition failed: {}", api_result)
                warning = f"api acquisition failed: {api_result}"
                warnings.append(warning)
                source_trace.append(
                    _source_trace_entry(
                        provider="api",
                        success=False,
                        warnings=[warning],
                        error=str(api_result),
                    )
                )
            else:
                api_items = api_result
                source_trace.append(
                    _source_trace_entry(
                        provider="api",
                        success=bool(api_items),
                        items_count=len(api_items),
                    )
                )
            if isinstance(firecrawl_result, Exception):
                logger.warning("web search acquisition failed: {}", firecrawl_result)
                warning = f"web search acquisition failed: {firecrawl_result}"
                warnings.append(warning)
                source_trace.append(
                    _source_trace_entry(
                        provider="web_search",
                        success=False,
                        warnings=[warning],
                        error=str(firecrawl_result),
                    )
                )
            else:
                firecrawl_links = firecrawl_result
                source_trace.append(
                    _source_trace_entry(
                        provider="web_search",
                        success=bool(firecrawl_links),
                        items_count=len(firecrawl_links),
                    )
                )

    candidates = _merge_and_dedupe(api_items, firecrawl_links)

    if not candidates:
        warnings.append("FETCH_NO_RESULT: no candidates from any source")
        return OnlineAcquisitionResponse(
            success=False,
            items=[],
            downloads=[],
            warnings=warnings,
            route=route,
            raw={"source_trace": [asdict(entry) for entry in source_trace]},
            candidate_links=[],
        ).model_dump()

    # Normalize API items
    normalized_items: list[OnlineAcquisitionItem] = []
    for item in candidates:
        provider = item.get("_source_provider", "unknown")
        try:
            normalized = normalize_items(provider, [item])
            if not normalized and item.get("_candidate_type") == "firecrawl":
                normalized = normalize_items("firecrawl", [item])
            normalized_items.extend(normalized)
        except Exception:
            try:
                normalized = normalize_items("firecrawl", [item])
                normalized_items.extend(normalized)
            except Exception:
                pass

    normalized_items, candidates = _apply_type_filter(
        normalized_items,
        candidates,
        list(request.literature_types) if request.literature_types else None,
    )

    clean_candidates = [{k: v for k, v in c.items() if not k.startswith("_")} for c in candidates]

    # === Search-only mode ===
    if request.action == "search":
        return OnlineAcquisitionResponse(
            success=bool(normalized_items),
            items=normalized_items,
            downloads=[],
            warnings=warnings,
            route=route,
            raw={"source_trace": [asdict(entry) for entry in source_trace]},
            candidate_links=clean_candidates,
        ).model_dump()

    # === Phase 2: Download ===
    download_results = await _download_candidates(candidates, download_path)

    if not download_results:
        warnings.append("FULLTEXT_UNAVAILABLE: no files downloaded")

    downloads = [
        {
            "file_path": dr.file_path,
            "source": dr.source,
            "doi": dr.doi,
            "pmcid": dr.pmcid,
            "url": dr.url,
            "warnings": dr.warnings,
        }
        for dr in download_results
    ]

    # === Phase 3: LLM Content Gate ===
    if request.relevance_gate and downloads:
        gate_result = await run_relevance_gate(
            query=query,
            downloads=downloads,
            delete_files=True,
            literature_types=list(request.literature_types) or None,
        )
        # Keep only relevant downloads (and those with errors — conservative)
        relevant_paths = {j.file_path for j in gate_result.judgments if j.relevant or j.error}
        filtered = [d for d in downloads if d.get("file_path") in relevant_paths]
        removed = gate_result.irrelevant
        if removed:
            warnings.append(f"RELEVANCE_GATE: {removed}/{gate_result.total} downloads removed as irrelevant")
            downloads = filtered

    return OnlineAcquisitionResponse(
        success=bool(download_results),
        items=normalized_items,
        downloads=downloads,
        warnings=warnings,
        route=route,
        candidate_links=clean_candidates,
    ).model_dump()


# ── Multilingual Acquisition Workflow ──────────────────────────────────────


async def _batch_parse_downloads(
    downloads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Submit downloaded PDFs to MinerU as a batch and attach parsed markdown.

    Each download dict gains a ``parsed_markdown`` (str) and ``parser_used``
    field. Files that MinerU fails to parse keep the dict but with empty
    markdown — the relevance gate then falls back to PDF text extraction.

    Imports ``create_parse_service`` lazily to avoid a hard dependency on
    PyO3 native extensions during plain ``import`` of this module (e.g.
    in unit tests that don't exercise parsing).
    """
    if not downloads:
        return downloads

    file_paths = [d.get("file_path", "") for d in downloads if d.get("file_path")]
    if not file_paths:
        return downloads

    try:
        from ._parse_service import (
            create_parse_service,
        )
    except Exception as exc:
        logger.warning("MinerU parse_service unavailable, skipping early parse: {}", exc)
        return downloads

    try:
        service = create_parse_service()
        batch = await service.parse_local_files(file_paths)
    except Exception as exc:
        logger.warning("MinerU batch parse failed, falling back to PDF extraction: {}", exc)
        return downloads

    # MinerU keys parsed results by basename of the uploaded file.
    parsed_by_name: dict[str, Any] = batch.results
    for d in downloads:
        fp = d.get("file_path") or ""
        if not fp:
            continue
        name = os.path.basename(fp)
        result = parsed_by_name.get(name)
        if result is None:
            # Try stem match as a fallback (MinerU may rename extension).
            stem = os.path.splitext(name)[0]
            for key in parsed_by_name:
                if os.path.splitext(key)[0] == stem:
                    result = parsed_by_name[key]
                    break
        if result is None:
            continue
        d["parsed_markdown"] = result.full_markdown
        d["parser_used"] = result.parser_used

    parsed_count = sum(1 for d in downloads if d.get("parsed_markdown"))
    logger.info(
        "early MinerU parse: {}/{} downloads parsed",
        parsed_count,
        len(downloads),
    )
    return downloads


async def search_language(
    query: str,
    language: str,
    candidate_limit: int = 15,
) -> list[dict[str, Any]]:
    """Search providers for a single language, return candidates tagged with search_lang."""
    plan = build_provider_plan(language=language)
    candidates = await search_parallel(
        query=query,
        plan=plan,
        concurrency=4,
        candidate_limit=candidate_limit,
    )
    for c in candidates:
        c["search_lang"] = language
    return candidates


async def multilingual_acquisition_workflow(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Multilingual acquisition pipeline.

    Phase 0: Translate query into 6 languages.
    Phase 1: Parallel search across all languages and providers.
    Phase 2: Download PDFs.
    Phase 3: Early MinerU parse + variant relevance gate (to be wired in Step 4-5).
    """
    # --- Validate request ---
    try:
        request = OnlineAcquisitionRequest(**payload)
    except Exception as exc:
        route = OnlineAcquisitionRouteInfo(prefer="auto", used="none", reason="invalid_request")
        return OnlineAcquisitionResponse(
            success=False,
            items=[],
            warnings=[f"invalid_request: {exc}"],
            route=route,
            candidate_links=[],
        ).model_dump()

    base_query = _build_query(request)
    warnings: list[str] = []
    source_trace: list[OnlineAcquisitionSourceTraceEntry] = []

    # === Phase 0: Query Translation ===
    try:
        translations = await translate_query(base_query)
        logger.info("query translated into {} languages", len(TARGET_LANGUAGES))
    except Exception as exc:
        logger.warning("query translation failed, falling back to single-language search: {}", exc)
        warnings.append(f"TRANSLATION_FAILED: {exc}")
        # Fall back to the original single-language workflow
        return await online_acquisition_workflow(payload)

    # === Phase 1: Parallel Multi-Lingual Search ===
    per_lang_limit = max(5, request.limit // len(TARGET_LANGUAGES))
    search_tasks = [
        search_language(query, lang, candidate_limit=per_lang_limit) for lang, query in translations.as_dict().items()
    ]
    lang_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    all_candidates: list[dict[str, Any]] = []
    for lang, result in zip(TARGET_LANGUAGES, lang_results):
        if isinstance(result, Exception):
            logger.warning("language '{}' search failed: {}", lang, result)
            warnings.append(f"SEARCH_FAILED_{lang}: {result}")
            source_trace.append(
                _source_trace_entry(
                    provider=f"multilingual-{lang}",
                    success=False,
                    warnings=[str(result)],
                    error=str(result),
                )
            )
        else:
            all_candidates.extend(result)
            source_trace.append(
                _source_trace_entry(
                    provider=f"multilingual-{lang}",
                    success=bool(result),
                    items_count=len(result),
                )
            )

    # Global dedup across all languages
    all_candidates = dedupe_candidates(all_candidates)
    all_candidates = rank_candidates(all_candidates, expected_title=base_query)

    if not all_candidates:
        warnings.append("FETCH_NO_RESULT: no candidates from any language")
        return OnlineAcquisitionResponse(
            success=False,
            items=[],
            downloads=[],
            warnings=warnings,
            route=OnlineAcquisitionRouteInfo(
                prefer=request.prefer,
                used="api",
                reason="multilingual_parallel",
                fallback_used=False,
            ),
            raw={"source_trace": [asdict(entry) for entry in source_trace]},
            candidate_links=[],
        ).model_dump()

    # Limit total candidates
    all_candidates = all_candidates[: request.limit]

    logger.info(
        "multilingual search: {} candidates from {} languages",
        len(all_candidates),
        len({c.get("search_lang", "") for c in all_candidates}),
    )

    # Normalize items
    normalized_items: list[OnlineAcquisitionItem] = []
    for item in all_candidates:
        provider = item.get("_source_provider", "unknown")
        try:
            normalized = normalize_items(provider, [item])
            normalized_items.extend(normalized)
        except Exception:
            try:
                normalized = normalize_items("crossref", [item])
                normalized_items.extend(normalized)
            except Exception:
                pass

    normalized_items, all_candidates = _apply_type_filter(
        normalized_items,
        all_candidates,
        list(request.literature_types) if request.literature_types else None,
    )

    clean_candidates = [{k: v for k, v in c.items() if not k.startswith("_")} for c in all_candidates]

    # === Search-only mode ===
    if request.action == "search":
        return OnlineAcquisitionResponse(
            success=bool(normalized_items),
            items=normalized_items,
            downloads=[],
            warnings=warnings,
            route=OnlineAcquisitionRouteInfo(
                prefer=request.prefer,
                used="api",
                reason="multilingual_parallel",
                fallback_used=False,
            ),
            raw={"source_trace": [asdict(entry) for entry in source_trace]},
            candidate_links=clean_candidates,
        ).model_dump()

    # === Phase 2: Download ===
    download_path = request.download_path
    download_results = await _download_candidates(all_candidates, download_path)

    if not download_results:
        warnings.append("FULLTEXT_UNAVAILABLE: no files downloaded")

    downloads = [
        {
            "file_path": dr.file_path,
            "source": dr.source,
            "doi": dr.doi,
            "pmcid": dr.pmcid,
            "url": dr.url,
            "warnings": dr.warnings,
            "search_lang": next(
                (
                    c.get("search_lang", "")
                    for c in all_candidates
                    if (dr.doi and c.get("doi") == dr.doi) or (dr.url and c.get("url") == dr.url)
                ),
                "",
            ),
        }
        for dr in download_results
    ]

    # === Phase 2.5: Early MinerU batch parse ===
    # Submit downloads to MinerU before the relevance gate so the LLM can
    # judge against rich extracted markdown instead of fitz-extracted PDF
    # text. Survivors carry their markdown forward as ``pre_parsed_markdown``
    # for downstream Phase 1 (which then skips MinerU re-parsing).
    if downloads:
        downloads = await _batch_parse_downloads(downloads)

    # === Phase 3: LLM Content Gate ===
    if request.relevance_gate and downloads:
        gate_result = await run_relevance_gate(
            query=base_query,
            downloads=downloads,
            delete_files=True,
            literature_types=list(request.literature_types) or None,
        )
        relevant_paths = {j.file_path for j in gate_result.judgments if j.relevant or j.error}
        filtered = [d for d in downloads if d.get("file_path") in relevant_paths]
        removed = gate_result.irrelevant
        if removed:
            warnings.append(f"RELEVANCE_GATE: {removed}/{gate_result.total} downloads removed as irrelevant")
            downloads = filtered

    return OnlineAcquisitionResponse(
        success=bool(download_results),
        items=normalized_items,
        downloads=downloads,
        warnings=warnings,
        route=OnlineAcquisitionRouteInfo(
            prefer=request.prefer,
            used="api",
            reason="multilingual_parallel",
            fallback_used=False,
        ),
        raw={"source_trace": [asdict(entry) for entry in source_trace]},
        candidate_links=clean_candidates,
    ).model_dump()
