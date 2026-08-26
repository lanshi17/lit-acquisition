"""Provider dispatch gateway.

Routes a provider request to the right backend (pure-Python search backends,
dedicated service clients, or the optional Rust ``net_io`` extension), applies
per-provider deadlines, classifies errors into actionable warning codes, and
retries transient failures. Bulk content download lives in :mod:`net.download`;
this module only fetches metadata.
"""

from __future__ import annotations

import asyncio
import threading
import time as _time
from typing import Any

import httpx
from loguru import logger

from ._rust_io import net_io
from .config import get_config
from .health import get_health_tracker
from .models import (
    OnlineAcquisitionGatewayRequest,
    OnlineAcquisitionGatewayResult,
    OnlineAcquisitionSourceTraceEntry,
)
from .net.security import redact_secrets

# Unicode hyphen/dash variants that should be normalized to ASCII hyphen in DOIs
_HYPHEN_CHARS = "‐‑‒–—―⁃−﹘﹣－"
_HYPHEN_TABLE = str.maketrans(_HYPHEN_CHARS, "-" * len(_HYPHEN_CHARS))


def _normalize_doi(doi: str) -> str:
    """Normalize unicode hyphen/dash variants to ASCII hyphen in DOIs."""
    return doi.translate(_HYPHEN_TABLE)



async def _await_blocking_daemon(fn: Any, timeout: float) -> Any:
    """Run a blocking callable on a *daemon* thread and await its result.

    ``asyncio.to_thread`` uses the default (non-daemon) executor, whose
    threads keep the interpreter alive at exit. When a deadline cancels
    the wait — e.g. J-STAGE's client retrying internally for minutes —
    that would hang process shutdown long after the workflow finished.
    Daemon threads do not block exit; the abandoned call simply dies
    with the process.
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[Any] = loop.create_future()

    def _settle(value: Any = None, exc: BaseException | None = None) -> None:
        if fut.done():
            return
        if exc is not None:
            fut.set_exception(exc)
        else:
            fut.set_result(value)

    def _worker() -> None:
        try:
            value = fn()
        except BaseException as exc:  # settle the future with any failure
            try:
                loop.call_soon_threadsafe(_settle, None, exc)
            except RuntimeError:
                pass  # loop already closed; nothing to settle
            return
        try:
            loop.call_soon_threadsafe(_settle, value, None)
        except RuntimeError:
            pass

    thread = threading.Thread(target=_worker, daemon=True, name="lit-blocking-provider")
    thread.start()
    return await asyncio.wait_for(fut, timeout=timeout)


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



def _unpaywall_pdf_url(record: dict[str, Any]) -> str | None:
    """Pick the best OA URL from an Unpaywall record.

    Considers ``best_oa_location`` plus every entry in ``oa_locations``,
    preferring a direct PDF link (``url_for_pdf``) over a landing page
    (``url``) so downloads get bytes instead of an HTML interstitial.
    """
    locations: list[dict[str, Any]] = []
    best_oa = record.get("best_oa_location")
    if isinstance(best_oa, dict):
        locations.append(best_oa)
    oa_locations = record.get("oa_locations")
    if isinstance(oa_locations, list):
        locations.extend(loc for loc in oa_locations if isinstance(loc, dict))
    for key in ("url_for_pdf", "url"):
        for loc in locations:
            url = loc.get(key)
            if url:
                return url
    return None


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
        # Unpaywall best_oa_location / oa_locations (keyword/DOI search shape).
        # Prefer a direct PDF link (url_for_pdf) over a landing page so the
        # download phase gets bytes, not an HTML interstitial.
        oa_url = _unpaywall_pdf_url(item)
        if oa_url:
            return oa_url
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


def _failure_result(
    provider: str,
    error: Exception | str,
    action: str = "search",
    warning: str | None = None,
) -> OnlineAcquisitionGatewayResult:
    if warning is None:
        warning = _classify_provider_error(provider, error) if isinstance(error, Exception) else f"{provider}_error:{error}"
    warnings = [warning]
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


def _timed_failure(
    provider: str,
    action: str,
    start: float,
    warning: str,
    error: str,
) -> OnlineAcquisitionGatewayResult:
    elapsed = (_time.monotonic() - start) * 1000
    get_health_tracker().record(provider, success=False, latency_ms=elapsed)
    trace = OnlineAcquisitionSourceTraceEntry(
        provider=provider,
        attempt=1,
        action=action,
        success=False,
        items_count=0,
        downloads_count=0,
        warnings=[warning],
        error=error,
    )
    return OnlineAcquisitionGatewayResult(
        provider=provider,
        success=False,
        items=[],
        downloads=[],
        warnings=[warning],
        source_trace=[trace],
    )


async def _search_jstage_via_pyjstage(
    request: OnlineAcquisitionGatewayRequest,
) -> OnlineAcquisitionGatewayResult:
    """Search J-STAGE using pyjstage-py312 library (bypasses broken Rust endpoint)."""
    start = _time.monotonic()
    try:
        try:
            from pyjstage.pyjstage import Pyjstage
        except ImportError as exc:
            elapsed = (_time.monotonic() - start) * 1000
            get_health_tracker().record("jstage", success=False, latency_ms=elapsed)
            return _failure_result(
                "jstage",
                exc,
                "search",
                warning="CONFIG_MISSING:jstage:pyjstage2 package not installed (pip install pyjstage2)",
            )

        pj = Pyjstage()
        query = (request.query or "").strip()
        if not query:
            return _failure_result("jstage", ValueError("empty query"), "search")

        limit = min(request.limit or 20, 100)
        # pyjstage is a blocking library; run it on a daemon thread so the
        # gateway deadline (asyncio.wait_for upstream) can cancel promptly
        # without stalling the event loop — and so an abandoned call does
        # not hold the interpreter open at exit.
        result = await _await_blocking_daemon(lambda: pj.search(article=query, count=limit), timeout=300)

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
        logger.warning("pyjstage search failed: {}", redact_secrets(str(exc)))
        return _failure_result("jstage", exc, "search")


async def _search_semantic_scholar(
    request: OnlineAcquisitionGatewayRequest,
) -> OnlineAcquisitionGatewayResult:
    """Search Semantic Scholar via Python httpx client."""
    start = _time.monotonic()
    try:
        from .providers.semantic_scholar import get_semantic_scholar_service

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
        logger.warning("semantic_scholar search failed: {}", redact_secrets(str(exc)))
        return _failure_result("semantic_scholar", exc, "search")


async def _search_clinical_trials(
    request: OnlineAcquisitionGatewayRequest,
) -> OnlineAcquisitionGatewayResult:
    """Search ClinicalTrials.gov via Python httpx client."""
    start = _time.monotonic()
    try:
        from .providers.clinical_trials import get_clinical_trials_service

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
        logger.warning("clinical_trials search failed: {}", redact_secrets(str(exc)))
        return _failure_result("clinical_trials", exc, "search")


async def _search_zenodo(
    request: OnlineAcquisitionGatewayRequest,
) -> OnlineAcquisitionGatewayResult:
    """Search Zenodo via Python httpx client."""
    start = _time.monotonic()
    try:
        from .providers.zenodo import get_zenodo_service

        svc = get_zenodo_service()
        query = (request.query or "").strip()
        if not query:
            return _failure_result("zenodo", ValueError("empty query"), "search")

        limit = min(request.limit or 20, 100)
        items = await svc.search(query, limit=limit)

        # Zenodo is a general OSS repository: drop datasets/software/images and
        # keep only publication-type records (articles, preprints, etc.) so the
        # search results are actual literature rather than data dumps.
        items = [
            it
            for it in items
            if ((it.get("metadata") or {}).get("resource_type") or {}).get("type") == "publication"
        ]

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
        logger.warning("zenodo search failed: {}", redact_secrets(str(exc)))
        return _failure_result("zenodo", exc, "search")


async def _search_pubmed(
    request: OnlineAcquisitionGatewayRequest,
) -> OnlineAcquisitionGatewayResult:
    """Search PubMed E-utilities via the Python service.

    PubMed is the canonical biomedical index; the federated plans include
    it as a first-class channel (the M1 baseline uses the same service).
    E-utilities without an API key allows ~3 requests/sec, so we retry
    429/5xx with a short backoff instead of failing a whole variant.
    """
    start = _time.monotonic()
    try:
        from .providers.pubmed import get_pubmed_service

        svc = get_pubmed_service()
        query = (request.query or "").strip()
        if not query:
            return _failure_result("pubmed", ValueError("empty query"), "search")

        limit = min(request.limit or 20, 100)
        candidates = None
        for attempt in range(3):
            try:
                candidates = await svc.search_candidates(query, candidate_limit=limit)
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise

        # esummary-compatible dicts (normalize_pmc handles this format).
        items = []
        for c in candidates or []:
            articleids = [{"idtype": "pmid", "value": c.pmid}]
            if c.doi:
                articleids.append({"idtype": "doi", "value": c.doi})
            if c.pmcid:
                articleids.append({"idtype": "pmc", "value": c.pmcid})
            items.append(
                {
                    "uid": c.pmid,
                    "title": c.title,
                    "fulljournalname": c.journal,
                    "source": c.journal,
                    "pubdate": c.pub_date,
                    "articleids": articleids,
                    "sortpubdate": c.pub_date,
                }
            )

        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record("pubmed", success=True, latency_ms=elapsed)
        trace = OnlineAcquisitionSourceTraceEntry(
            provider="pubmed",
            attempt=1,
            action="search",
            success=True,
            items_count=len(items),
            downloads_count=0,
            warnings=[],
        )
        return OnlineAcquisitionGatewayResult(
            provider="pubmed",
            success=True,
            items=items,
            downloads=[],
            warnings=[],
            source_trace=[trace],
        )
    except Exception as exc:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record("pubmed", success=False, latency_ms=elapsed)
        logger.warning("pubmed search failed: {}", redact_secrets(str(exc)))
        return _failure_result("pubmed", exc, "search")


# ── Python-first provider dispatch ───────────────────────────────────────
#
# Every provider is backed by a pure-Python httpx implementation
# (py_providers / dedicated services), so the toolkit no longer depends
# on the optional Rust extension (net_io). The Rust path is kept only
# for download actions of providers without a Python download backend.
#
# Warning-code taxonomy (agent-actionable):
#   CONFIG_MISSING:<provider>:<reason>   permanent — set the named config
#   PROVIDER_UNAVAILABLE:<provider>:...  permanent — no usable backend
#   TIMEOUT:<provider>:...               transient — deadline exceeded
#   NETWORK_ERROR:<provider>:...         transient — connection problem
#   PROVIDER_HTTP_<status>:<provider>    transient for 429/5xx, else permanent


def _provider_deadline(request: OnlineAcquisitionGatewayRequest) -> float:
    if request.timeout and request.timeout > 0:
        return float(request.timeout)
    return get_config().http.provider_timeout


async def _with_deadline(
    coro: Any,
    provider: str,
    action: str,
    timeout: float,
) -> OnlineAcquisitionGatewayResult:
    """Await a provider coroutine, converting deadline overruns into
    clean failure results (the wrapped coroutine records its own
    health on success and on errors it lives to see)."""
    start = _time.monotonic()
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record(provider, success=False, latency_ms=elapsed)
        warning = f"TIMEOUT:{provider}:exceeded {timeout:.0f}s deadline"
        trace = OnlineAcquisitionSourceTraceEntry(
            provider=provider,
            attempt=1,
            action=action,
            success=False,
            items_count=0,
            downloads_count=0,
            warnings=[warning],
            error="deadline exceeded",
        )
        return OnlineAcquisitionGatewayResult(
            provider=provider,
            success=False,
            items=[],
            downloads=[],
            warnings=[warning],
            source_trace=[trace],
        )


async def _search_via_python_backend(
    request: OnlineAcquisitionGatewayRequest,
    timeout: float,
) -> OnlineAcquisitionGatewayResult:
    """Run a py_providers backend with a deadline, health recording and
    error classification."""
    from .providers.backends import PY_SEARCH_BACKENDS
    from .providers.errors import ProviderConfigError

    provider = request.provider
    backend = PY_SEARCH_BACKENDS[provider]
    identifiers = {k: v for k, v in (request.identifiers or {}).items() if v}
    if identifiers.get("doi"):
        identifiers["doi"] = _normalize_doi(identifiers["doi"])

    start = _time.monotonic()
    try:
        items = await asyncio.wait_for(
            backend(request.query or "", request.limit, identifiers, request.params or {}),
            timeout=timeout,
        )
    except TimeoutError:
        return _timed_failure(
            provider,
            "search",
            start,
            f"TIMEOUT:{provider}:exceeded {timeout:.0f}s deadline",
            "deadline exceeded",
        )
    except ProviderConfigError as exc:
        return _timed_failure(provider, "search", start, str(exc), exc.reason)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        return _timed_failure(
            provider,
            "search",
            start,
            redact_secrets(f"PROVIDER_HTTP_{status}:{provider}:{_http_error_detail(exc)}"),
            redact_secrets(str(exc)),
        )
    except httpx.HTTPError as exc:
        return _timed_failure(
            provider,
            "search",
            start,
            redact_secrets(f"NETWORK_ERROR:{provider}:{type(exc).__name__}: {exc}"),
            redact_secrets(str(exc)),
        )
    except Exception as exc:
        return _timed_failure(
            provider, "search", start, redact_secrets(f"{provider}_error:{exc}"), redact_secrets(str(exc))
        )

    items = list(items or [])
    elapsed = (_time.monotonic() - start) * 1000
    get_health_tracker().record(provider, success=True, latency_ms=elapsed)
    trace = OnlineAcquisitionSourceTraceEntry(
        provider=provider,
        attempt=1,
        action="search",
        success=True,
        items_count=len(items),
        downloads_count=0,
        warnings=[],
    )
    return OnlineAcquisitionGatewayResult(
        provider=provider,
        success=True,
        items=items,
        downloads=[],
        warnings=[],
        source_trace=[trace],
    )


def _http_error_detail(exc: httpx.HTTPStatusError) -> str:
    resp = exc.response
    if resp is None:
        return "no response"
    try:
        body = resp.text[:200]
    except Exception:
        body = ""
    return body.replace("\n", " ").strip() or resp.reason_phrase or "http error"


def _classify_provider_error(provider: str, exc: Exception) -> str:
    """Map an exception to an actionable warning code.

    Produces the same taxonomy the Python backends emit so agents can
    branch on prefix regardless of which layer failed:

    * ``PROVIDER_HTTP_429:<provider>:...`` — rate limited (set an API key
      for higher limits, or back off);
    * ``PROVIDER_HTTP_<status>:<provider>`` — other HTTP errors;
    * ``NETWORK_ERROR:<provider>:...`` / ``TIMEOUT:<provider>:...``;
    * ``<provider>_error:...`` — anything else.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else 0
        detail = _http_error_detail(exc)
        if status == 429:
            hint = ""
            if provider == "semantic_scholar":
                hint = " (set LIT_SEMANTIC_SCHOLAR_API_KEY for higher rate limits)"
            elif provider == "pubmed" or provider == "pmc":
                hint = " (set LIT_PUBMED_API_KEY for higher rate limits)"
            return f"PROVIDER_HTTP_429:{provider}:rate limited{hint}: {detail[:100]}"
        return f"PROVIDER_HTTP_{status}:{provider}:{detail[:120]}"
    if isinstance(exc, asyncio.TimeoutError):
        return f"TIMEOUT:{provider}:deadline exceeded"
    if isinstance(exc, httpx.TimeoutException):
        return f"TIMEOUT:{provider}:{type(exc).__name__}"
    if isinstance(exc, httpx.HTTPError):
        # str(exc) may embed the request URL, which can carry api_key/email
        # query params (PubMed, Unpaywall) — always redact before surfacing.
        return redact_secrets(f"NETWORK_ERROR:{provider}:{type(exc).__name__}: {exc}")
    return redact_secrets(f"{provider}_error:{exc}")


async def _download_unpaywall_python(
    request: OnlineAcquisitionGatewayRequest,
) -> OnlineAcquisitionGatewayResult:
    """Unpaywall OA resolution (download action) in pure Python."""
    provider = "unpaywall"
    start = _time.monotonic()
    doi = _normalize_doi(str((request.identifiers or {}).get("doi") or ""))
    if not doi:
        return _failure_result(provider, ValueError("unpaywall download requires a DOI"), "download")
    cfg = get_config()
    email = cfg.unpaywall.email.strip()
    if not email:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record(provider, success=False, latency_ms=elapsed)
        warning = "CONFIG_MISSING:unpaywall:no email configured (set LIT_UNPAYWALL_EMAIL or UNPAYWALL_EMAIL)"
        trace = OnlineAcquisitionSourceTraceEntry(
            provider=provider,
            attempt=1,
            action="download",
            success=False,
            items_count=0,
            downloads_count=0,
            warnings=[warning],
            error="config missing",
        )
        return OnlineAcquisitionGatewayResult(
            provider=provider, success=False, items=[], downloads=[], warnings=[warning], source_trace=[trace]
        )
    try:
        base = cfg.unpaywall.base_url.rstrip("/")
        client = _get_pooled_client(base)
        resp = await client.get(f"{base}/{doi}", params={"email": email})
        if resp.status_code == 404:
            elapsed = (_time.monotonic() - start) * 1000
            get_health_tracker().record(provider, success=True, latency_ms=elapsed)
            return OnlineAcquisitionGatewayResult(
                provider=provider, success=True, items=[], downloads=[], warnings=[f"unpaywall_no_oa:{doi}"]
            )
        resp.raise_for_status()
        record = resp.json()
        oa_url = resolve_oa_url(
            OnlineAcquisitionGatewayResult(provider=provider, success=True, items=[record], warnings=[])
        )
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record(provider, success=True, latency_ms=elapsed)
        downloads = [{"pdf_url": oa_url, "doi": doi, "source": "unpaywall"}] if oa_url else []
        trace = OnlineAcquisitionSourceTraceEntry(
            provider=provider,
            attempt=1,
            action="download",
            success=True,
            items_count=1,
            downloads_count=len(downloads),
            warnings=[],
        )
        return OnlineAcquisitionGatewayResult(
            provider=provider,
            success=True,
            items=[record],
            downloads=downloads,
            warnings=[],
            source_trace=[trace],
        )
    except Exception as exc:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record(provider, success=False, latency_ms=elapsed)
        logger.warning("unpaywall download failed: {}", redact_secrets(str(exc)))
        return _failure_result(provider, exc, "download")


def _get_pooled_client(base_url: str):
    from .net.pool import get_shared_client, resolve_provider_proxy

    return get_shared_client(proxy=resolve_provider_proxy(base_url))


async def call_provider(request: OnlineAcquisitionGatewayRequest) -> OnlineAcquisitionGatewayResult:
    """Call a single provider.

    Search actions are served by pure-Python backends (httpx) for every
    provider, so the toolkit works without the optional Rust extension.
    Download actions use the Python Unpaywall resolver, with the Rust
    ``net_io`` path retained where available for the remaining cases.
    Each call runs under a per-provider deadline
    (``HttpPoolConfig.provider_timeout`` by default).
    """
    from .providers.backends import PY_SEARCH_BACKENDS

    provider = request.provider
    action = request.action
    timeout = _provider_deadline(request)

    if action == "search":
        if provider == "jstage":
            return await _with_deadline(_search_jstage_via_pyjstage(request), provider, action, timeout)
        if provider == "pubmed":
            return await _with_deadline(_search_pubmed(request), provider, action, timeout)
        if provider == "semantic_scholar":
            return await _with_deadline(_search_semantic_scholar(request), provider, action, timeout)
        if provider == "clinical_trials":
            return await _with_deadline(_search_clinical_trials(request), provider, action, timeout)
        if provider == "zenodo":
            return await _with_deadline(_search_zenodo(request), provider, action, timeout)
        if provider in PY_SEARCH_BACKENDS:
            return await _search_via_python_backend(request, timeout)
    elif action == "download":
        if provider == "unpaywall":
            return await _with_deadline(_download_unpaywall_python(request), provider, action, timeout)

    start = _time.monotonic()
    if net_io is None:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record(request.provider, success=False, latency_ms=elapsed)
        return _failure_result(
            request.provider,
            RuntimeError("no Python backend and net_io extension not installed"),
            request.action,
            warning=(
                f"PROVIDER_UNAVAILABLE:{request.provider}:"
                f"no Python backend for action '{request.action}' and the optional rust-io extension is not installed"
            ),
        )

    params = _build_fetch_params(request)
    # Provider APIs are mostly international — use the configured proxy.
    proxy = get_config().network.proxy or None
    try:
        raw_result = await asyncio.wait_for(
            net_io.fetch_one(
                provider=request.provider,
                action=request.action,
                params=params,
                proxy=proxy,
            ),
            timeout=timeout,
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
    except TimeoutError:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record(request.provider, success=False, latency_ms=elapsed)
        return _failure_result(
            request.provider,
            TimeoutError(f"exceeded {timeout:.0f}s deadline"),
            request.action,
            warning=f"TIMEOUT:{request.provider}:exceeded {timeout:.0f}s deadline",
        )
    except Exception as exc:
        elapsed = (_time.monotonic() - start) * 1000
        get_health_tracker().record(request.provider, success=False, latency_ms=elapsed)
        return _failure_result(request.provider, exc, request.action)


_NON_RETRYABLE_PREFIXES = ("CONFIG_MISSING", "PROVIDER_UNAVAILABLE", "PROVIDER_HTTP_4")


def _is_retryable(result: OnlineAcquisitionGatewayResult) -> bool:
    """Classify a failed result: retry transient errors only.

    Deterministic failures (missing configuration, unsupported backend,
    4xx client errors other than 429) are never retried — retrying them
    only adds latency without any chance of success.
    """
    for warning in result.warnings:
        if warning.startswith("PROVIDER_HTTP_429"):
            continue
        if any(warning.startswith(prefix) for prefix in _NON_RETRYABLE_PREFIXES):
            return False
    return True


async def call_provider_with_retry(
    request: OnlineAcquisitionGatewayRequest,
    max_attempts: int = 2,
) -> OnlineAcquisitionGatewayResult:
    """Call a provider with retry logic and source_trace aggregation.

    Backoff is error-aware: rate limits (429) and server errors wait
    longer between attempts than plain network glitches, and
    deterministic failures are never retried (see :func:`_is_retryable`).
    """
    all_traces: list[OnlineAcquisitionSourceTraceEntry] = []
    all_warnings: list[str] = []
    last_result: OnlineAcquisitionGatewayResult | None = None

    for attempt in range(1, max_attempts + 1):
        result = await call_provider(request)
        # Update attempt number in traces
        for trace in result.source_trace:
            trace.attempt = attempt
        all_traces.extend(result.source_trace)
        all_warnings.extend(result.warnings)

        if result.success:
            result.source_trace = all_traces
            result.warnings = all_warnings
            return result
        last_result = result
        if not _is_retryable(result):
            break  # deterministic failure - retrying only wastes time
        if attempt < max_attempts:
            rate_limited = any("PROVIDER_HTTP_429" in w for w in result.warnings)
            await asyncio.sleep(2.0 * attempt if rate_limited else 0.5 * attempt)

    if last_result:
        last_result.source_trace = all_traces
        last_result.warnings = all_warnings
        return last_result
    return _failure_result(request.provider, RuntimeError("no result"), request.action)


async def search_provider(
    provider: str,
    query: str | None = None,
    identifiers: dict[str, str | None] | None = None,
    limit: int = 20,
    raw: bool = False,
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> OnlineAcquisitionGatewayResult:
    """Search a single provider.

    ``timeout`` bounds this single provider call (falls back to
    ``HttpPoolConfig.provider_timeout``); it keeps one slow upstream from
    stalling an agent's tool call.
    """
    request = OnlineAcquisitionGatewayRequest(
        provider=provider,
        action="search",
        query=query,
        identifiers=identifiers or {},
        limit=limit,
        raw=raw,
        params=params or {},
        timeout=timeout,
    )
    return await call_provider_with_retry(request)
