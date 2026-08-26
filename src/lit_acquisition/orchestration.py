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
import time as _time
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from loguru import logger

from .algorithms import (
    build_provider_plan,
    classify_item,
    dedupe_candidates,
    lexical_relevance,
    normalize_candidate,
    rank_candidates,
)
from .gateway import (
    _normalize_doi,
    resolve_oa_url,
    search_provider,
)
from .health import get_health_tracker
from .llm import (
    TARGET_LANGUAGES,
    neural_rerank,
    rerank_enabled,
    run_relevance_gate,
    translate_query,
)
from .models import (
    DownloadResult,
    OnlineAcquisitionGatewayResult,
    OnlineAcquisitionItem,
    OnlineAcquisitionRequest,
    OnlineAcquisitionResponse,
    OnlineAcquisitionRouteInfo,
    OnlineAcquisitionSourceTraceEntry,
    ProviderPlanItem,
)
from .net.download import download_file_from_url
from .normalize import DOI_PATTERN, normalize_items
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
    "semantic_scholar",
    "clinical_trials",
    "zenodo",
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
    phase_timeout: float = 30.0,
    trace_sink: list[OnlineAcquisitionSourceTraceEntry] | None = None,
    warning_sink: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Phase 1a: Search API providers in parallel, return raw items with metadata.

    Speed behavior:

    * providers the health tracker has circuit-broken are skipped with a
      ``SKIPPED`` trace entry instead of consuming the deadline;
    * each provider runs under its own gateway deadline;
    * the fan-out stops early (cancelling in-flight searches) once
      ``max(limit * 2, 30)`` items are collected, so one slow upstream
      cannot dominate the phase;
    * the whole phase is bounded by ``phase_timeout``.
    """
    from .health import get_health_tracker

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

    tracker = get_health_tracker()
    active: list[str] = []
    for provider in providers:
        if tracker.should_skip(provider):
            entry = _source_trace_entry(
                provider=provider,
                success=False,
                warnings=[f"SKIPPED:{provider}:circuit open after repeated failures"],
                error="circuit_open",
            )
            if trace_sink is not None:
                trace_sink.append(entry)
            logger.info("acquire_links: skipping unhealthy provider {}", provider)
            continue
        active.append(provider)

    sem = asyncio.Semaphore(8)

    async def _search_one(provider: str) -> OnlineAcquisitionGatewayResult | None:
        async with sem:
            try:
                result = await search_provider(
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
            if trace_sink is not None:
                trace_sink.extend(result.source_trace)
            if warning_sink is not None:
                warning_sink.extend(result.warnings)
            return result

    tasks = {asyncio.create_task(_search_one(p)) for p in active}
    all_items: list[dict[str, Any]] = []
    pending = set(tasks)
    # Early-stop gates: enough items AND enough providers already
    # completed. The completion floor keeps fast-but-shallow providers
    # (crossref/doaj return full pages quickly) from cancelling the
    # slower, higher-signal ones (pubmed/europepmc) before they answer;
    # the item target stops one pathological slow provider from holding
    # the phase open once results are ample.
    early_target = max(limit * 3, 40)
    min_done = max(3, len(active) // 2)
    done_count = 0
    end = _time.monotonic() + max(phase_timeout, 1.0)
    try:
        while pending:
            remaining = end - _time.monotonic()
            if remaining <= 0:
                if warning_sink is not None:
                    warning_sink.append(f"PHASE_TIMEOUT:api:link acquisition cut off at {phase_timeout:.0f}s budget")
                break
            done, pending = await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                done_count += 1
                try:
                    result = task.result()
                except Exception as exc:
                    logger.debug("api search task failed: {}", exc)
                    continue
                if result and result.success:
                    for item in result.items:
                        if isinstance(item, dict):
                            item["_source_provider"] = result.provider
                            all_items.append(item)
            if len(all_items) >= early_target and done_count >= min_done:
                break
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

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
                    timeout=ws.timeout,
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
                    timeout=ws.timeout,
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
            "web search skipped: no TAVILY_API_KEY, SERPAPI_API_KEY, or LIT_FIRECRAWL_API_KEY/WEB_SEARCH_API_KEY configured"
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
            elif isinstance(sr, Exception):
                logger.warning("scrape_links failed: {}", sr)

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


def _dedupe_warnings(warnings: list[str]) -> list[str]:
    """Order-preserving dedup; identical provider failures repeat across
    retry attempts and language fan-outs and would otherwise flood the
    agent with noise."""
    seen: set[str] = set()
    out: list[str] = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def _finalize_response(
    response: OnlineAcquisitionResponse,
    *,
    started_at: float,
    source_trace: list[OnlineAcquisitionSourceTraceEntry],
    compact: bool = False,
) -> dict[str, Any]:
    """Attach agent-friendly ``summary`` + ``diagnostics`` and apply compact mode.

    The diagnostics block gives a caller (typically an LLM agent) a
    structured, per-provider outcome map so it can decide whether to
    retry, reconfigure, or relax filters — without parsing free-form
    warnings.
    """
    elapsed_ms = round((_time.monotonic() - started_at) * 1000)
    provider_reports: list[dict[str, Any]] = []
    items_by_provider: dict[str, int] = {}
    for entry in source_trace:
        if entry.error == "circuit_open" or (entry.attempt == 0 and not entry.success):
            status = "skipped"
        else:
            status = "ok" if entry.success else "failed"
        provider_reports.append(
            {
                "provider": entry.provider,
                "status": status,
                "attempts": max(entry.attempt, 1),
                "items": entry.items_count,
                "downloads": entry.downloads_count,
                "warnings": list(entry.warnings or []),
                "error": entry.error,
            }
        )
        if entry.success and entry.provider not in ("api", "web_search"):
            items_by_provider[entry.provider] = items_by_provider.get(entry.provider, 0) + entry.items_count

    response.diagnostics = {
        "elapsed_ms": elapsed_ms,
        "items_total": len(response.items),
        "downloads_total": len(response.downloads),
        "providers": provider_reports,
    }

    ok_count = sum(1 for r in provider_reports if r["status"] == "ok")
    failed = sorted({r["provider"] for r in provider_reports if r["status"] == "failed"})
    skipped = sorted({r["provider"] for r in provider_reports if r["status"] == "skipped"})
    top = sorted(items_by_provider.items(), key=lambda kv: kv[1], reverse=True)[:3]

    parts = [
        f"{'ok' if response.success else 'failed'}: {len(response.items)} items"
        + (f", {len(response.downloads)} downloads" if response.downloads else "")
        + f" in {elapsed_ms / 1000:.1f}s ({ok_count} providers)"
    ]
    if top:
        parts.append("top: " + ", ".join(f"{p}({n})" for p, n in top))
    if failed:
        parts.append("failed: " + ",".join(failed[:6]))
    if skipped:
        parts.append("skipped: " + ",".join(skipped[:6]))
    response.summary = "; ".join(parts)

    if compact:
        response.candidate_links = []
        response.raw = None
    return response.model_dump()


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

    Concurrency is bounded (semaphore) and the Unpaywall route is skipped
    entirely when no email is configured — otherwise every DOI candidate
    burned a request on a guaranteed HTTP 422.
    """
    from .config import get_config

    unpaywall_configured = bool(get_config().unpaywall.email.strip())
    sem = asyncio.Semaphore(6)

    async def _download_one(candidate: dict[str, Any]) -> DownloadResult | None:
        doi = _coerce_str(candidate.get("doi") or candidate.get("DOI")).strip() or None
        pmid = _coerce_str(candidate.get("pmid")).strip() or None
        if not pmid and isinstance(candidate.get("identifiers"), dict):
            pmid = _coerce_str(candidate["identifiers"].get("pmid")).strip() or None
        pmcid = _coerce_str(candidate.get("pmcid")).strip() or None
        if not pmcid and isinstance(candidate.get("identifiers"), dict):
            pmcid = _coerce_str(candidate["identifiers"].get("pmcid")).strip() or None
        url = _coerce_str(candidate.get("url") or candidate.get("URL")).strip() or None
        title = _coerce_str(candidate.get("title")) or "untitled"
        url_for_hash = url or doi or pmcid or title or "unknown"
        url_hash = hashlib.md5(url_for_hash.encode()).hexdigest()[:8]
        filename_stem = f"{re.sub(r'[^\w\-]', '_', title)[:70]}_{url_hash}"

        async with sem:
            # Route 1: DOI → unpaywall OA resolution
            if doi and unpaywall_configured:
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

            # Route 2: PMCID -> EuropePMC render (primary) -> PMC direct (fallback)
            if pmcid:
                pmcid_url_candidates = [
                    f"https://europepmc.org/articles/{pmcid}?pdf=render",
                    f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/",
                ]
                try:
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
                except Exception as exc:
                    logger.warning("pmc download failed for {}: {}", pmcid, exc)

            # Route 3: Direct URL download
            if url:
                try:
                    file_path, final_url, warns = await download_file_from_url(url, download_path, filename_stem)
                    if file_path:
                        return DownloadResult(
                            file_path=file_path,
                            source=candidate.get("_source_provider", "direct"),
                            url=final_url,
                            warnings=warns,
                        )
                except Exception as exc:
                    logger.warning("direct download failed for {}: {}", url, exc)

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
            summary=f"failed: invalid request ({exc})",
        ).model_dump()
    workflow_start = _time.monotonic()
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
            api_items = await _acquire_links_api(
                query=query,
                identifiers=id_params,
                limit=request.limit,
                phase_timeout=request.timeout,
                trace_sink=source_trace,
                warning_sink=warnings,
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
                    phase_timeout=request.timeout,
                    trace_sink=source_trace,
                    warning_sink=warnings,
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
            api_task = _acquire_links_api(
                query=query,
                identifiers=id_params,
                limit=request.limit,
                phase_timeout=request.timeout,
                trace_sink=source_trace,
                warning_sink=warnings,
            )
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
    warnings = _dedupe_warnings(warnings)

    if not candidates:
        warnings.append("FETCH_NO_RESULT: no candidates from any source")
        return _finalize_response(
            OnlineAcquisitionResponse(
                success=False,
                items=[],
                downloads=[],
                warnings=warnings,
                route=route,
                raw={"source_trace": [asdict(entry) for entry in source_trace]},
                candidate_links=[],
            ),
            started_at=workflow_start,
            source_trace=source_trace,
            compact=request.compact,
        )

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
            except Exception as exc:
                warnings.append(f"NORMALIZE_FAILED: {provider}: {exc}")

    normalized_items, candidates = _apply_type_filter(
        normalized_items,
        candidates,
        list(request.literature_types) if request.literature_types else None,
    )

    clean_candidates = [{k: v for k, v in c.items() if not k.startswith("_")} for c in candidates]

    # === Search-only mode ===
    if request.action == "search":
        return _finalize_response(
            OnlineAcquisitionResponse(
                success=bool(normalized_items),
                items=normalized_items,
                downloads=[],
                warnings=warnings,
                route=route,
                raw={"source_trace": [asdict(entry) for entry in source_trace]},
                candidate_links=clean_candidates,
            ),
            started_at=workflow_start,
            source_trace=source_trace,
            compact=request.compact,
        )

    # === Phase 2: Download ===
    # Rank raw candidates by query relevance (stable: ties keep provider
    # arrival order) so the bounded download slice targets the best items,
    # then cap the fan-out — a few extra to absorb per-candidate failures —
    # instead of fetching every discovered link.
    def _candidate_title(c: dict[str, Any]) -> str:
        return _coerce_str(c.get("title") or c.get("article_title") or c.get("article_title_en"))

    candidates.sort(key=lambda c: lexical_relevance(query, _candidate_title(c)), reverse=True)
    download_budget = max(request.limit, 10) + 5
    download_results = await _download_candidates(candidates[:download_budget], download_path)

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
        warnings.extend(gate_result.warnings)
        if removed:
            warnings.append(f"RELEVANCE_GATE: {removed}/{gate_result.total} downloads removed as irrelevant")
            downloads = filtered
    return _finalize_response(
        OnlineAcquisitionResponse(
            success=bool(downloads),
            items=normalized_items,
            downloads=downloads,
            warnings=warnings,
            route=route,
            candidate_links=clean_candidates,
        ),
        started_at=workflow_start,
        source_trace=source_trace,
        compact=request.compact,
    )


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
    trace_sink: list[OnlineAcquisitionSourceTraceEntry] | None = None,
) -> list[dict[str, Any]]:
    """Search providers for a single language, return candidates tagged with search_lang."""
    plan = build_provider_plan(language=language)
    candidates = await search_parallel(
        query=query,
        plan=plan,
        concurrency=4,
        candidate_limit=candidate_limit,
        trace_sink=trace_sink,
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
            summary=f"failed: invalid request ({exc})",
        ).model_dump()
    workflow_start = _time.monotonic()

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
        search_language(query, lang, candidate_limit=per_lang_limit, trace_sink=source_trace)
        for lang, query in translations.as_dict().items()
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
    if rerank_enabled():
        from .llm import neural_rerank

        all_candidates = await neural_rerank(base_query, all_candidates)

    if not all_candidates:
        warnings.append("FETCH_NO_RESULT: no candidates from any language")
        return _finalize_response(
            OnlineAcquisitionResponse(
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
                raw={
                    "source_trace": [asdict(entry) for entry in source_trace],
                    # Persist the per-language queries so downstream consumers
                    # (e.g. benchmark runs) can audit translation fidelity.
                    "translations": translations.as_dict(),
                },
                candidate_links=[],
            ),
            started_at=workflow_start,
            source_trace=source_trace,
            compact=request.compact,
        )

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
        # Candidates from search_parallel/_normalize_candidate carry the source
        # under `provider`; the single-language path uses `_source_provider`.
        provider = item.get("_source_provider") or item.get("provider", "unknown")
        try:
            normalized = normalize_items(provider, [item])
            normalized_items.extend(normalized)
        except Exception:
            try:
                normalized = normalize_items("crossref", [item])
                normalized_items.extend(normalized)
            except Exception as exc:
                warnings.append(f"NORMALIZE_FAILED: {provider}: {exc}")

    normalized_items, all_candidates = _apply_type_filter(
        normalized_items,
        all_candidates,
        list(request.literature_types) if request.literature_types else None,
    )

    clean_candidates = [{k: v for k, v in c.items() if not k.startswith("_")} for c in all_candidates]

    # === Search-only mode ===
    if request.action == "search":
        return _finalize_response(
            OnlineAcquisitionResponse(
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
                raw={
                    "source_trace": [asdict(entry) for entry in source_trace],
                    "translations": translations.as_dict(),
                },
                candidate_links=clean_candidates,
            ),
            started_at=workflow_start,
            source_trace=source_trace,
            compact=request.compact,
        )

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
        warnings.extend(gate_result.warnings)
        if removed:
            warnings.append(f"RELEVANCE_GATE: {removed}/{gate_result.total} downloads removed as irrelevant")
            downloads = filtered

    return _finalize_response(
        OnlineAcquisitionResponse(
            success=bool(downloads),
            items=normalized_items,
            warnings=warnings,
            route=OnlineAcquisitionRouteInfo(
                prefer=request.prefer,
                used="api",
                reason="multilingual_parallel",
                fallback_used=False,
            ),
            raw={"source_trace": [asdict(entry) for entry in source_trace]},
            candidate_links=clean_candidates,
        ),
        started_at=workflow_start,
        source_trace=source_trace,
        compact=request.compact,
    )


# --- Concurrent provider fan-out (I/O orchestration) ---


async def search_multilingual(
    *,
    target: str,
    disease: str,
    language: str = "auto",
    candidate_limit: int = 15,
    provider_hints: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Search across multiple providers with language-based routing.

    Calls gateway.search_provider directly (not workflow).
    """
    query = f"{target} {disease} case report".strip()
    if not query:
        return []

    plan = build_provider_plan(language=language, provider_hints=provider_hints)
    plan = get_health_tracker().reorder_plan(plan)
    collected: list[dict[str, Any]] = []
    preferred_provider = plan[0]["provider"] if plan else None

    for plan_item in plan:
        if get_health_tracker().should_skip(plan_item["provider"]):
            continue
        result = await search_provider(
            provider=plan_item["provider"],
            query=query,
            limit=candidate_limit,
        )
        items = normalize_items(result.provider, result.items) if result.success else []
        for item in items:
            collected.append(normalize_candidate(item.model_dump(), plan_item))

        collected = dedupe_candidates(collected)
        collected = rank_candidates(collected, expected_title=target, preferred_provider=preferred_provider)
        if len(collected) >= candidate_limit:
            return collected[:candidate_limit]

    return rank_candidates(collected, expected_title=target, preferred_provider=preferred_provider)[:candidate_limit]


async def search_parallel(
    *,
    query: str,
    plan: list[ProviderPlanItem],
    concurrency: int = 4,
    candidate_limit: int = 15,
    identifiers: dict[str, str] | None = None,
    trace_sink: list[OnlineAcquisitionSourceTraceEntry] | None = None,
    early_stop: bool = True,
) -> list[dict[str, Any]]:
    """Search multiple providers concurrently, merge and dedupe results.

    Optimizations over a plain fan-out:

    * circuit breaker - providers that are failing persistently (health
      tracker) are skipped instead of consuming the deadline on every
      request;
    * early stop - once ``2 x candidate_limit`` raw candidates are
      collected, the remaining in-flight searches are cancelled so one
      slow upstream cannot dominate wall time;
    * per-provider deadlines are enforced inside the gateway.
    """
    tracker = get_health_tracker()
    active_plan: list[ProviderPlanItem] = []
    skipped: list[str] = []
    for item in plan:
        if tracker.should_skip(item["provider"]):
            skipped.append(item["provider"])
            if trace_sink is not None:
                trace_sink.append(
                    OnlineAcquisitionSourceTraceEntry(
                        provider=item["provider"],
                        attempt=0,
                        action="search",
                        success=False,
                        items_count=0,
                        downloads_count=0,
                        warnings=[f"SKIPPED:{item['provider']}:circuit open after repeated failures"],
                        error="circuit_open",
                    )
                )
            continue
        active_plan.append(item)
    if skipped:
        logger.info("search_parallel: skipping unhealthy providers: {}", ",".join(skipped))

    sem = asyncio.Semaphore(concurrency)
    preferred_provider = active_plan[0]["provider"] if active_plan else None
    id_params = {k: v for k, v in (identifiers or {}).items() if v}

    async def _search_one(item: ProviderPlanItem) -> list[dict[str, Any]]:
        async with sem:
            result = await search_provider(
                provider=item["provider"],
                query=query,
                identifiers=id_params,
                limit=candidate_limit,
            )
            if trace_sink is not None:
                trace_sink.extend(result.source_trace)
            items = normalize_items(result.provider, result.items) if result.success else []
            return [normalize_candidate(i.model_dump(), item) for i in items]

    tasks = [asyncio.create_task(_search_one(item)) for item in active_plan]
    collected: list[dict[str, Any]] = []
    pending = set(tasks)
    # Early-stop gates (see _acquire_links_api for rationale): item target
    # plus a completed-provider floor for source diversity.
    early_stop_at = candidate_limit * 2
    min_done = max(2, len(active_plan) // 2)
    done_count = 0
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                done_count += 1
                try:
                    collected.extend(task.result())
                except Exception as exc:
                    logger.warning("provider search task failed: {}", exc)
            if early_stop and len(collected) >= early_stop_at and done_count >= min_done:
                break
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    collected = dedupe_candidates(collected)
    # Drop empty candidates (no title, DOI or URL): they cannot be shown to a
    # user nor downloaded, and only dilute relevance ranking.
    collected = [c for c in collected if (str(c.get("title") or "").strip() or c.get("doi") or c.get("url"))]
    ranked = rank_candidates(collected, expected_title=query, preferred_provider=preferred_provider)
    if rerank_enabled():
        ranked = await neural_rerank(query or "", ranked)
    return ranked[:candidate_limit]
