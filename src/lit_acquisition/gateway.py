"""Unified gateway — delegates HTTP I/O to net_io, Python handles downloads."""

from __future__ import annotations

import asyncio
import re
import time as _time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from loguru import logger

from ._rust_io import net_io
from .config import get_config
from .contracts import (
    OnlineAcquisitionGatewayRequest,
    OnlineAcquisitionGatewayResult,
    OnlineAcquisitionSourceTraceEntry,
)
from .provider_health import get_health_tracker
from .utils.ssrf import validate_url_safe
from .utils.text import sanitize_filename

_PDF_LINK_PATTERNS = [
    re.compile(r'href=["\']([^"\']*\.pdf[^"\']*)["\']', re.IGNORECASE),
    re.compile(r'href=["\']([^"\']*download[^"\']*pdf[^"\']*)["\']', re.IGNORECASE),
]

# Unicode hyphen/dash variants that should be normalized to ASCII hyphen in DOIs
_HYPHEN_CHARS = "‐‑‒–—―⁃−﹘﹣－"
_HYPHEN_TABLE = str.maketrans(_HYPHEN_CHARS, "-" * len(_HYPHEN_CHARS))


def _normalize_doi(doi: str) -> str:
    """Normalize unicode hyphen/dash variants to ASCII hyphen in DOIs."""
    return doi.translate(_HYPHEN_TABLE)


def _extract_pdf_links_from_html(html: str, base_url: str) -> list[str]:
    links: list[str] = []
    for pattern in _PDF_LINK_PATTERNS:
        for match in pattern.finditer(html):
            href = match.group(1)
            absolute = urljoin(base_url, href)
            if absolute not in links:
                links.append(absolute)
    return links


def _choose_item(
    items: list[dict[str, Any]],
    selected_index: int,
    selected_title: str | None,
    title_keys: list[str],
) -> dict[str, Any] | None:
    """Select an item by title match or index."""

    def _read_key(item: dict[str, Any], key: str) -> str:
        if "." not in key:
            return str(item.get(key) or "").strip()
        current: Any = item
        for part in key.split("."):
            if not isinstance(current, dict):
                return ""
            current = current.get(part)
        return str(current or "").strip()

    if selected_title:
        wanted = str(selected_title).strip().lower()
        for item in items:
            for key in title_keys:
                title = _read_key(item, key).lower()
                if title and wanted in title:
                    return item
    if 0 <= selected_index < len(items):
        return items[selected_index]
    return None


async def download_file_from_url(
    url: str,
    download_path: str,
    filename_stem: str,
) -> tuple[str | None, str | None, list[str]]:
    """Download a file from a direct URL. Handles HTML→PDF redirect.

    If the URL returns PDF bytes (magic ``%PDF``), saves directly.
    If the URL returns HTML, extracts PDF links from the page and retries
    each candidate (preserves existing _download_pdf_from_candidates behavior).

    Proxy routing: international sites go through the configured proxy;
    mainland China sites connect directly (see ``NetworkConfig``).

    Args:
        url: Direct download URL.
        download_path: Directory to save the file.
        filename_stem: Base filename (without extension).

    Returns:
        (file_path, final_url, warnings) tuple.
    """
    warnings: list[str] = []
    target = Path(download_path) / f"{sanitize_filename(filename_stem)}.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    proxy_resolver = get_config().network.resolve_proxy_for_url

    # Build candidate queue: start with the given URL
    queue: list[str] = [url]
    visited: set[str] = set()

    while queue:
        current_url = queue.pop(0)
        if current_url in visited:
            continue
        visited.add(current_url)

        try:
            validate_url_safe(current_url)
        except ValueError as ssrf_exc:
            warnings.append(f"ssrf_blocked:{current_url}:{ssrf_exc}")
            continue

        proxy = proxy_resolver(current_url)

        # Try Rust download first (faster, has built-in retry)
        if net_io is not None:
            try:
                result = await net_io.download_file(current_url, timeout_ms=30_000, proxy=proxy)
                status = result.get("status_code", 0)
                file_bytes: bytes = result.get("bytes", b"")
                final_url: str = result.get("final_url", current_url)

                if status == 0 or status >= 400:
                    warnings.append(f"download_http_{status}:{current_url}")
                    continue

                if final_url != current_url:
                    try:
                        validate_url_safe(final_url)
                    except ValueError as ssrf_exc:
                        warnings.append(f"ssrf_blocked_redirect:{current_url}:{ssrf_exc}")
                        continue

                if file_bytes and file_bytes[:4] == b"%PDF":
                    target.write_bytes(file_bytes)
                    return str(target), final_url, warnings

                # Non-PDF content — might be HTML with PDF link
                if file_bytes and (b"<html" in file_bytes[:2048].lower()):
                    extra_links = _extract_pdf_links_from_html(
                        file_bytes.decode("utf-8", errors="replace"), final_url or current_url
                    )
                    for link in extra_links:
                        if link not in visited:
                            queue.append(link)
                    continue

                warnings.append(f"non_pdf_content:{current_url}")
                continue

            except Exception as exc:
                logger.debug("rust download_file failed for {}: {}", current_url, exc)
                warnings.append(f"rust_download_error:{current_url}:{exc}")
                # Fall through to httpx fallback

        # Fallback: httpx (handles HTML→PDF redirect same as existing code)
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=60, follow_redirects=True) as client:
                resp = await client.get(current_url)
                resp.raise_for_status()

                content = resp.content or b""
                content_type = str(resp.headers.get("content-type") or "").lower()
                final_url = str(resp.url)

                if final_url != current_url:
                    try:
                        validate_url_safe(final_url)
                    except ValueError as ssrf_exc:
                        warnings.append(f"ssrf_blocked_redirect:{current_url}:{ssrf_exc}")
                        continue

                if content.startswith(b"%PDF"):
                    target.write_bytes(content)
                    return str(target), final_url, warnings

                if "html" in content_type or b"<html" in content[:2048].lower():
                    extra_links = _extract_pdf_links_from_html(resp.text or "", final_url or current_url)
                    for link in extra_links:
                        if link not in visited:
                            queue.append(link)
                    continue

                warnings.append(f"non_pdf_content_type:{content_type or 'unknown'}:{current_url}")

        except Exception as exc:
            warnings.append(f"download_error:{current_url}:{exc}")

    return None, None, warnings


def resolve_oa_url(result: OnlineAcquisitionGatewayResult) -> str | None:
    """Extract OA download URL from a gateway result.

    Inspects result.downloads for pdf_url entries (returned by unpaywall, doaj, etc.)
    and result.items for embedded download links (e.g., europepmc fullTextUrlList).
    """
    # Check downloads first (unpaywall, doaj, jstage pattern)
    for dl in result.downloads:
        if isinstance(dl, dict):
            dl_url = dl.get("pdf_url") or dl.get("url")
            if dl_url:
                return dl_url

    # Check items for embedded URLs (europepmc fullTextUrlList, crossref link)
    for item in result.items:
        if not isinstance(item, dict):
            continue
        # Semantic Scholar openAccessPdf
        oa_pdf = item.get("openAccessPdf")
        if isinstance(oa_pdf, dict) and oa_pdf.get("url"):
            return oa_pdf.get("url")
        # EuropePMC fullTextUrlList
        ftl = item.get("fullTextUrlList")
        if isinstance(ftl, dict):
            for ft in ftl.get("fullTextUrl", []):
                if isinstance(ft, dict) and ft.get("documentStyle") == "pdf":
                    return ft.get("url")
        # Crossref link array
        links = item.get("link")
        if isinstance(links, list):
            for link in links:
                if isinstance(link, dict) and link.get("content-type") == "application/pdf":
                    return link.get("URL")
        # PMC pmcid → construct URL
        pmcid = item.get("pmcid")
        if isinstance(pmcid, str) and pmcid.startswith("PMC"):
            return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"

    return None


def _build_fetch_params(request: OnlineAcquisitionGatewayRequest) -> dict[str, Any]:
    """Convert OnlineAcquisitionGatewayRequest to net_io.fetch_one params dict."""
    params: dict[str, Any] = {
        "query": request.query or "",
        "limit": request.limit,
        "raw": request.raw,
        "selected_index": request.selected_index,
    }
    if request.selected_title is not None:
        params["selected_title"] = request.selected_title
    if request.detail_link is not None:
        params["detail_link"] = request.detail_link
    if request.identifiers:
        identifiers = {}
        for k, v in request.identifiers.items():
            if v is None:
                continue
            if k == "doi":
                v = _normalize_doi(v)
            identifiers[k] = v
        params["identifiers"] = identifiers
    # Pass through extra provider params (year_range, is_oa, etc.)
    if request.params:
        for k, v in request.params.items():
            if k not in params:
                params[k] = v
    return {k: v for k, v in params.items() if v is not None}


def _rust_result_to_gateway(
    provider: str,
    result: dict[str, Any],
    trace: OnlineAcquisitionSourceTraceEntry | None = None,
) -> OnlineAcquisitionGatewayResult:
    """Convert net_io FetchResult dict to OnlineAcquisitionGatewayResult."""
    gateway_result = OnlineAcquisitionGatewayResult(
        provider=provider,
        success=bool(result.get("success")),
        items=list(result.get("items") or []),
        downloads=list(result.get("downloads") or []),
        warnings=list(result.get("warnings") or []),
        raw=result.get("raw"),
        meta=result.get("meta"),
    )
    if trace:
        gateway_result.source_trace = [trace]
    return gateway_result


def _failure_result(provider: str, error: Exception, action: str = "search") -> OnlineAcquisitionGatewayResult:
    warnings = [f"{provider}_error:{error}"]
    trace = OnlineAcquisitionSourceTraceEntry(
        provider=provider,
        attempt=1,
        action=action,
        success=False,
        items_count=0,
        downloads_count=0,
        warnings=warnings,
        error=str(error),
    )
    return OnlineAcquisitionGatewayResult(
        provider=provider,
        success=False,
        items=[],
        downloads=[],
        warnings=warnings,
        source_trace=[trace],
    )


async def _search_jstage_via_pyjstage(
    request: OnlineAcquisitionGatewayRequest,
) -> OnlineAcquisitionGatewayResult:
    """Search J-STAGE using pyjstage-py312 library (bypasses broken Rust endpoint)."""
    start = _time.monotonic()
    try:
        from pyjstage.pyjstage import Pyjstage

        pj = Pyjstage()
        query = (request.query or "").strip()
        if not query:
            return _failure_result("jstage", ValueError("empty query"), "search")

        limit = min(request.limit or 20, 100)
        result = pj.search(article=query, count=limit)

        items: list[dict[str, Any]] = []
        for entry in result.entries:
            items.append(
                {
                    "article_title_en": (entry.article_title or {}).get("en", ""),
                    "article_title_ja": (entry.article_title or {}).get("ja", ""),
                    "material_title_en": (entry.material_title or {}).get("en", ""),
                    "material_title_ja": (entry.material_title or {}).get("ja", ""),
                    "doi": entry.doi or "",
                    "link": entry.link or "",
                    "issn": entry.issn or "",
                    "eissn": entry.eissn or "",
                    "pubyear": entry.pubyear or "",
                }
            )

        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record("jstage", success=True, latency_ms=elapsed)
        trace = OnlineAcquisitionSourceTraceEntry(
            provider="jstage",
            attempt=1,
            action="search",
            success=True,
            items_count=len(items),
            downloads_count=0,
            warnings=[],
        )
        return OnlineAcquisitionGatewayResult(
            provider="jstage",
            success=True,
            items=items,
            downloads=[],
            warnings=[],
            source_trace=[trace],
        )
    except Exception as exc:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record("jstage", success=False, latency_ms=elapsed)
        logger.warning("pyjstage search failed: {}", exc)
        return _failure_result("jstage", exc, "search")


async def _search_semantic_scholar(
    request: OnlineAcquisitionGatewayRequest,
) -> OnlineAcquisitionGatewayResult:
    """Search Semantic Scholar via Python httpx client."""
    start = _time.monotonic()
    try:
        from .semantic_scholar import get_semantic_scholar_service

        svc = get_semantic_scholar_service()
        query = (request.query or "").strip()
        if not query:
            return _failure_result("semantic_scholar", ValueError("empty query"), "search")

        limit = min(request.limit or 20, 100)
        items = await svc.search(query, limit=limit)

        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record("semantic_scholar", success=True, latency_ms=elapsed)
        trace = OnlineAcquisitionSourceTraceEntry(
            provider="semantic_scholar",
            attempt=1,
            action="search",
            success=True,
            items_count=len(items),
            downloads_count=0,
            warnings=[],
        )
        return OnlineAcquisitionGatewayResult(
            provider="semantic_scholar",
            success=True,
            items=items,
            downloads=[],
            warnings=[],
            source_trace=[trace],
        )
    except Exception as exc:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record("semantic_scholar", success=False, latency_ms=elapsed)
        logger.warning("semantic_scholar search failed: {}", exc)
        return _failure_result("semantic_scholar", exc, "search")


async def _search_clinical_trials(
    request: OnlineAcquisitionGatewayRequest,
) -> OnlineAcquisitionGatewayResult:
    """Search ClinicalTrials.gov via Python httpx client."""
    start = _time.monotonic()
    try:
        from .clinical_trials import get_clinical_trials_service

        svc = get_clinical_trials_service()
        query = (request.query or "").strip()
        if not query:
            return _failure_result("clinical_trials", ValueError("empty query"), "search")

        limit = min(request.limit or 20, 1000)
        items = await svc.search(query, limit=limit)

        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record("clinical_trials", success=True, latency_ms=elapsed)
        trace = OnlineAcquisitionSourceTraceEntry(
            provider="clinical_trials",
            attempt=1,
            action="search",
            success=True,
            items_count=len(items),
            downloads_count=0,
            warnings=[],
        )
        return OnlineAcquisitionGatewayResult(
            provider="clinical_trials",
            success=True,
            items=items,
            downloads=[],
            warnings=[],
            source_trace=[trace],
        )
    except Exception as exc:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record("clinical_trials", success=False, latency_ms=elapsed)
        logger.warning("clinical_trials search failed: {}", exc)
        return _failure_result("clinical_trials", exc, "search")


async def _search_zenodo(
    request: OnlineAcquisitionGatewayRequest,
) -> OnlineAcquisitionGatewayResult:
    """Search Zenodo via Python httpx client."""
    start = _time.monotonic()
    try:
        from .zenodo import get_zenodo_service

        svc = get_zenodo_service()
        query = (request.query or "").strip()
        if not query:
            return _failure_result("zenodo", ValueError("empty query"), "search")

        limit = min(request.limit or 20, 100)
        items = await svc.search(query, limit=limit)

        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record("zenodo", success=True, latency_ms=elapsed)
        trace = OnlineAcquisitionSourceTraceEntry(
            provider="zenodo",
            attempt=1,
            action="search",
            success=True,
            items_count=len(items),
            downloads_count=0,
            warnings=[],
        )
        return OnlineAcquisitionGatewayResult(
            provider="zenodo",
            success=True,
            items=items,
            downloads=[],
            warnings=[],
            source_trace=[trace],
        )
    except Exception as exc:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record("zenodo", success=False, latency_ms=elapsed)
        logger.warning("zenodo search failed: {}", exc)
        return _failure_result("zenodo", exc, "search")


async def call_provider(request: OnlineAcquisitionGatewayRequest) -> OnlineAcquisitionGatewayResult:
    """Call a single provider via net_io.fetch_one.

    J-STAGE search is routed through pyjstage-py312 in Python because the
    Rust provider hits a broken HTML endpoint.  Download still goes through
    Rust (pdf_candidates logic is correct).

    Semantic Scholar, ClinicalTrials.gov, and Zenodo are Python-only
    providers (no Rust implementation) - they use httpx directly.
    """
    if request.provider == "jstage" and request.action == "search":
        return await _search_jstage_via_pyjstage(request)

    if request.provider == "semantic_scholar" and request.action == "search":
        return await _search_semantic_scholar(request)

    if request.provider == "clinical_trials" and request.action == "search":
        return await _search_clinical_trials(request)

    if request.provider == "zenodo" and request.action == "search":
        return await _search_zenodo(request)

    start = _time.monotonic()
    if net_io is None:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record(request.provider, success=False, latency_ms=elapsed)
        return _failure_result(
            request.provider,
            RuntimeError("net_io not available"),
            request.action,
        )

    params = _build_fetch_params(request)
    # Provider APIs are mostly international — use the configured proxy.
    proxy = get_config().network.proxy or None
    try:
        raw_result = await net_io.fetch_one(
            provider=request.provider,
            action=request.action,
            params=params,
            proxy=proxy,
        )
        elapsed = (_time.monotonic() - start) * 1000
        success = bool(raw_result.get("success"))
        get_health_tracker().record(request.provider, success=success, latency_ms=elapsed)
        trace = OnlineAcquisitionSourceTraceEntry(
            provider=request.provider,
            attempt=1,
            action=request.action,
            success=success,
            items_count=len(raw_result.get("items") or []),
            downloads_count=len(raw_result.get("downloads") or []),
            warnings=list(raw_result.get("warnings") or []),
        )
        return _rust_result_to_gateway(request.provider, raw_result, trace)
    except Exception as exc:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record(request.provider, success=False, latency_ms=elapsed)
        return _failure_result(request.provider, exc, request.action)


async def call_provider_with_retry(
    request: OnlineAcquisitionGatewayRequest,
    max_attempts: int = 2,
) -> OnlineAcquisitionGatewayResult:
    """Call a provider with retry logic and source_trace aggregation."""
    all_traces: list[OnlineAcquisitionSourceTraceEntry] = []
    last_result: OnlineAcquisitionGatewayResult | None = None

    for attempt in range(1, max_attempts + 1):
        result = await call_provider(request)
        # Update attempt number in traces
        for trace in result.source_trace:
            trace.attempt = attempt
        all_traces.extend(result.source_trace)

        if result.success and (result.items or result.downloads):
            result.source_trace = all_traces
            return result
        last_result = result
        if attempt < max_attempts:
            await asyncio.sleep(0.5 * attempt)

    if last_result:
        last_result.source_trace = all_traces
        return last_result
    return _failure_result(request.provider, RuntimeError("no result"), request.action)


async def search_provider(
    provider: str,
    query: str | None = None,
    identifiers: dict[str, str | None] | None = None,
    limit: int = 20,
    raw: bool = False,
    params: dict[str, Any] | None = None,
) -> OnlineAcquisitionGatewayResult:
    """Search a single provider."""
    request = OnlineAcquisitionGatewayRequest(
        provider=provider,
        action="search",
        query=query,
        identifiers=identifiers or {},
        limit=limit,
        raw=raw,
        params=params or {},
    )
    return await call_provider_with_retry(request)
