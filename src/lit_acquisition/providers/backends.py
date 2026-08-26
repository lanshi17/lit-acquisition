"""Pure-Python provider backends.

Every federation provider gets an ``httpx`` implementation here so the
toolkit works end-to-end **without** the optional Rust extension
(``rust_io`` / ``net_io``). Previously, providers without a Python
backend failed hard with ``RuntimeError("net_io not available")`` —
13 of 18 providers were unusable on a plain install.

Each backend returns raw dicts in the exact shape the normalizer
registry (``normalizers.NORMALIZER_MAP``) already understands, so no
downstream changes are needed per provider.

Error taxonomy:

* :class:`ProviderConfigError` — permanent, configuration missing
  (e.g. Unpaywall without an email, BASE/CORE without API keys).
  Callers must NOT retry these; they surface as ``CONFIG_MISSING``
  warnings.
* :class:`httpx.HTTPStatusError` / network errors — transient; the
  gateway may retry with backoff.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx
from loguru import logger

from ..config import get_config
from ..net.pool import get_shared_client, resolve_provider_proxy
from .errors import ProviderConfigError

BackendFn = Callable[..., Awaitable[list[dict[str, Any]]]]


def _client_for(base_url: str, timeout: float | None = None) -> httpx.AsyncClient:
    """Pooled client routed through the configured proxy for *base_url*."""
    proxy = resolve_provider_proxy(base_url)
    return get_shared_client(proxy=proxy, timeout=timeout)


def _mailto() -> str:
    """Contact email for polite-pool APIs (Crossref/OpenAlex/Unpaywall)."""
    return get_config().unpaywall.email.strip()


async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> Any:
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


# ── Crossref ─────────────────────────────────────────────────────────────

_CROSSREF_BASE = "https://api.crossref.org"
# NOTE: `language` is intentionally absent — the /works route rejects it
# as a select field (HTTP 400 "select-not-available").
_CROSSREF_SELECT = "DOI,title,author,container-title,issued,created,URL,type,subject,ISSN,publisher,link"


def _crossref_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    mailto = _mailto()
    if mailto:
        headers["User-Agent"] = f"lit-acquisition/0.3 (mailto:{mailto})"
    return headers


async def search_crossref(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Crossref bibliographic search (and DOI lookup when given)."""
    doi = (identifiers or {}).get("doi")
    client = _client_for(_CROSSREF_BASE)
    headers = _crossref_headers()

    if doi:
        resp = await client.get(f"{_CROSSREF_BASE}/works/{quote(doi, safe='')}", headers=headers)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        message = resp.json().get("message") or {}
        return [message] if message else []

    term = (query or "").strip()
    if not term:
        return []
    payload = await _get_json_with_headers(
        client,
        f"{_CROSSREF_BASE}/works",
        {
            "query.bibliographic": term,
            "rows": max(1, min(limit, 100)),
            "select": _CROSSREF_SELECT,
        },
        headers,
    )
    items = ((payload or {}).get("message") or {}).get("items") or []
    return [it for it in items if isinstance(it, dict)]


async def _get_json_with_headers(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
    headers: dict[str, str],
) -> Any:
    resp = await client.get(url, params=params, headers=headers or None)
    resp.raise_for_status()
    return resp.json()


# ── OpenAlex ─────────────────────────────────────────────────────────────

_OPENALEX_BASE = "https://api.openalex.org"


async def search_openalex(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """OpenAlex works search (and DOI lookup when given)."""
    doi = (identifiers or {}).get("doi")
    client = _client_for(_OPENALEX_BASE)
    common: dict[str, Any] = {}
    mailto = _mailto()
    if mailto:
        common["mailto"] = mailto

    if doi:
        resp = await client.get(f"{_OPENALEX_BASE}/works/https://doi.org/{quote(doi, safe='')}", params=common)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return [resp.json()]

    term = (query or "").strip()
    if not term:
        return []
    payload = await _get_json(
        client,
        f"{_OPENALEX_BASE}/works",
        {"search": term, "per-page": max(1, min(limit, 100)), **common},
    )
    return [w for w in (payload or {}).get("results") or [] if isinstance(w, dict)]


# ── EuropePMC ────────────────────────────────────────────────────────────


async def search_europepmc(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """EuropePMC search via the EBI REST mirror (JSON).

    The ``www.europepmc.org/search`` frontend returns HTML even with
    ``format=json``; the EBI mirror serves the documented JSON REST API.
    """
    base = get_config().europepmc.base_url.rstrip("/")
    ids = identifiers or {}
    doi = ids.get("doi")
    pmcid = ids.get("pmcid")
    pmid = ids.get("pmid")
    if doi:
        term = f'DOI:"{doi}"'
    elif pmcid:
        # PMCID must be unquoted: PMCID:"PMC123" matches nothing.
        pmcid_value = pmcid if str(pmcid).upper().startswith("PMC") else f"PMC{pmcid}"
        term = f"PMCID:{pmcid_value}"
    elif pmid:
        term = f'EXT_ID:"{pmid}"'
    else:
        term = (query or "").strip()
    if not term:
        return []

    client = _client_for(base)
    payload = await _get_json(
        client,
        f"{base}/search",
        {
            "query": term,
            "format": "json",
            "pageSize": max(1, min(limit, 100)),
            "resultType": "core",
        },
    )
    results = ((payload or {}).get("resultList") or {}).get("result") or []
    normalized: list[dict[str, Any]] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        journal_title = r.get("journalTitle")
        if not journal_title:
            journal_info = r.get("journalInfo") or {}
            journal_title = ((journal_info.get("journal") or {}).get("title")) or ""
        author_string = r.get("authorString") or ""
        normalized.append(
            {
                "title": r.get("title"),
                "authorString": author_string,
                "authors": [a.strip() for a in author_string.split(",") if a.strip()],
                "journalTitle": journal_title,
                "doi": r.get("doi"),
                "pmcid": r.get("pmcid"),
                "pmid": r.get("pmid"),
                "pubYear": r.get("pubYear"),
                "fullTextUrlList": r.get("fullTextUrlList"),
                "isOpenAccess": r.get("isOpenAccess"),
                "hasPDF": r.get("hasPDF"),
                "source": r.get("source"),
            }
        )
    return normalized


# ── PMC (eutils, db=pmc) ─────────────────────────────────────────────────


async def search_pmc(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """PMC search via NCBI E-utilities (esearch db=pmc + esummary)."""
    from .pubmed import get_pubmed_service

    svc = get_pubmed_service()
    term = (query or "").strip()
    pmcid = (identifiers or {}).get("pmcid")
    if pmcid:
        term = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
    if not term:
        return []

    cfg_base = get_config().pubmed.base_url.rstrip("/")
    retmax = max(1, min(limit, 100))
    esearch_params: dict[str, Any] = {
        "db": "pmc",
        "term": term,
        "retmax": retmax,
        "retmode": "json",
    }
    if svc.api_key:
        esearch_params["api_key"] = svc.api_key

    client = _client_for(cfg_base)
    esearch_resp = await client.get(f"{cfg_base}/esearch.fcgi", params=esearch_params)
    esearch_resp.raise_for_status()
    ids: list[str] = esearch_resp.json().get("esearchresult", {}).get("idlist", []) or []
    if not ids:
        return []

    summary_params: dict[str, Any] = {"db": "pmc", "id": ",".join(ids), "retmode": "json"}
    if svc.api_key:
        summary_params["api_key"] = svc.api_key
    summary_resp = await client.get(f"{cfg_base}/esummary.fcgi", params=summary_params)
    summary_resp.raise_for_status()
    records = summary_resp.json().get("result", {})

    items: list[dict[str, Any]] = []
    for uid in ids:
        row = records.get(uid, {}) or {}
        title = row.get("title") or row.get("articletitle") or ""
        pmc_id = f"PMC{uid}" if not str(uid).upper().startswith("PMC") else str(uid)
        articleids = [{"idtype": "pmcid", "value": pmc_id}]
        doi = ""
        for aid in row.get("articleids", []) or []:
            idtype = str(aid.get("idtype", "")).lower()
            value = str(aid.get("value", "")).strip()
            if idtype == "doi" and not doi:
                doi = value
                articleids.append({"idtype": "doi", "value": value})
        items.append(
            {
                "uid": uid,
                "title": str(title).strip(),
                "fulljournalname": row.get("journaltitle") or row.get("fulljournalname") or row.get("source") or "",
                "source": row.get("source") or "",
                "pubdate": row.get("pubdate") or "",
                "articleids": articleids,
            }
        )
    return items


# ── DOAJ ─────────────────────────────────────────────────────────────────

_DOAJ_BASE = "https://doaj.org/api"


async def search_doaj(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """DOAJ article search; maps ``bibjson`` to the normalizer shape."""
    doi = (identifiers or {}).get("doi")
    term = f'doi:"{doi}"' if doi else (query or "").strip()
    if not term:
        return []

    client = _client_for(_DOAJ_BASE)
    payload = await _get_json(
        client,
        f"{_DOAJ_BASE}/search/articles/{quote(term, safe='')}",
        {"pageSize": max(1, min(limit, 100))},
    )
    items: list[dict[str, Any]] = []
    for entry in (payload or {}).get("results") or []:
        bib = (entry or {}).get("bibjson") or {}
        if not isinstance(bib, dict):
            continue
        doi_value = ""
        issns: list[str] = []
        for ident in bib.get("identifier") or []:
            if not isinstance(ident, dict):
                continue
            id_type = str(ident.get("type", "")).lower()
            value = str(ident.get("id", "")).strip()
            if id_type == "doi" and not doi_value:
                doi_value = value
            elif id_type in ("issn", "eissn") and value:
                issns.append(value)
        links = [
            str(link.get("url")).strip()
            for link in bib.get("link") or []
            if isinstance(link, dict) and link.get("url")
        ]
        journal = bib.get("journal") or {}
        items.append(
            {
                "title": bib.get("title"),
                "journal_title": journal.get("title"),
                "publisher": journal.get("publisher"),
                "doi": doi_value,
                "url": links[0] if links else None,
                "links": links,
                "issns": issns,
                "year": bib.get("year"),
                "keywords": bib.get("keywords") or [],
                "author": bib.get("author") or [],
            }
        )
    return items


# ── Unpaywall ────────────────────────────────────────────────────────────


async def search_unpaywall(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Unpaywall OA resolution by DOI.

    Unpaywall is DOI-keyed: a keyword query without a DOI yields nothing,
    so we return ``[]`` without a network call (the provider remains in
    routing plans for identifier lookups but costs nothing for keyword
    fan-out). The API rejects requests without an ``email`` parameter
    (HTTP 422), so missing configuration raises :class:`ProviderConfigError`
    instead of burning requests.
    """
    doi = (identifiers or {}).get("doi")
    if not doi:
        return []

    email = _mailto()
    if not email:
        raise ProviderConfigError(
            "unpaywall",
            "no email configured (set LIT_UNPAYWALL_EMAIL or UNPAYWALL_EMAIL)",
        )

    base = get_config().unpaywall.base_url.rstrip("/")
    client = _client_for(base)
    resp = await client.get(f"{base}/{quote(doi, safe='')}", params={"email": email})
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return [resp.json()]


# ── arXiv ────────────────────────────────────────────────────────────────

_ARXIV_BASE = "https://export.arxiv.org/api/query"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _arxiv_search_query(query: str, mode: str = "AND") -> str:
    """Build an arXiv API search_query from free text.

    ``AND`` narrows across all tokens (precise but can be empty for
    long queries); ``OR`` broadens for the recall fallback pass.
    """
    tokens = [t.strip('"') for t in query.split() if t.strip()]
    tokens = tokens[:8]
    if not tokens:
        return ""
    return f" {mode} ".join(f"all:{quote(t, safe='')}" for t in tokens)


def parse_arxiv_atom(xml_text: str) -> list[dict[str, Any]]:
    """Parse an arXiv Atom feed into preprint-shaped dicts (testable offline)."""
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.debug("arxiv feed parse failed: {}", exc)
        return []

    for entry in root.findall("atom:entry", _ATOM_NS):
        title = " ".join((entry.findtext("atom:title", default="", namespaces=_ATOM_NS) or "").split())
        entry_id = (entry.findtext("atom:id", default="", namespaces=_ATOM_NS) or "").strip()
        # arXiv's feed emits http:// identifiers; prefer https:// so landing
        # pages and PDF downloads are encrypted in transit (and still work in
        # networks that block outbound port 80).
        if entry_id.startswith("http://arxiv.org/"):
            entry_id = "https://" + entry_id[len("http://") :]
        published = entry.findtext("atom:published", default="", namespaces=_ATOM_NS) or ""
        authors = [
            (name.text or "").strip()
            for name in entry.findall("atom:author/atom:name", _ATOM_NS)
            if name.text and name.text.strip()
        ]
        doi = (entry.findtext("arxiv:doi", default="", namespaces=_ATOM_NS) or "").strip()
        if not title:
            continue
        items.append(
            {
                "title": title,
                "authors": authors,
                "doi": doi,
                "url": entry_id,
                "year": published[:4] if len(published) >= 4 else "",
                "source": "arxiv",
            }
        )
    return items


async def search_arxiv(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """arXiv API keyword search (Atom XML).

    Tries the precise ``AND`` query first; if it yields nothing (common
    for long clinical queries where documents miss a token), retries once
    with ``OR`` so relevant preprints are still surfaced. Results are
    relevance-sorted either way.
    """
    term = (query or "").strip()
    if not term:
        return []
    client = _client_for(_ARXIV_BASE)
    for mode in ("AND", "OR"):
        search_query = _arxiv_search_query(term, mode=mode)
        if not search_query:
            return []
        resp = await client.get(
            _ARXIV_BASE,
            params={
                "search_query": search_query,
                "max_results": max(1, min(limit, 100)),
                "sortBy": "relevance",
                "sortOrder": "descending",
            },
        )
        resp.raise_for_status()
        items = parse_arxiv_atom(resp.text)
        if items:
            return items
        # arXiv asks for 3+ seconds between requests; fan-out makes immediate
        # retries a throttling (503) risk.
        await asyncio.sleep(1.0)
    return []


# ── bioRxiv / medRxiv (via EuropePMC preprint source) ───────────────────


async def _search_preprints_via_europepmc(
    query: str,
    limit: int,
    server: str,
) -> list[dict[str, Any]]:
    """bioRxiv/medRxiv expose no public keyword-search API (the bioRxiv
    API only lists by date / DOI). Both are indexed by EuropePMC under
    source ``PPR``, where the hosting server is recorded in
    ``bookOrReportDetails.publisher``. We search EuropePMC preprints and
    keep only records from the requested server.

    (Crossref's ``type:posted-content`` route was evaluated and rejected:
    it ignores keyword queries, so it cannot back a search provider.)
    """
    term = (query or "").strip()
    if not term:
        return []
    base = get_config().europepmc.base_url.rstrip("/")
    client = _client_for(base)
    payload = await _get_json(
        client,
        f"{base}/search",
        {
            "query": f"{term} AND SRC:PPR",
            "format": "json",
            "pageSize": max(1, min(limit * 3, 100)),
            "resultType": "core",
        },
    )
    results = ((payload or {}).get("resultList") or {}).get("result") or []
    wanted = server.lower()
    items: list[dict[str, Any]] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        publisher = str(((r.get("bookOrReportDetails") or {}).get("publisher")) or "").strip().lower()
        if publisher != wanted:
            continue
        author_string = r.get("authorString") or ""
        doi = r.get("doi") or ""
        full_text = r.get("fullTextUrlList") or {}
        url = ""
        for ft in full_text.get("fullTextUrl") or []:
            if isinstance(ft, dict) and ft.get("url"):
                url = ft["url"]
                break
        items.append(
            {
                "title": r.get("title"),
                "authors": [a.strip() for a in author_string.split(",") if a.strip()],
                "doi": doi,
                "url": url or (f"https://doi.org/{doi}" if doi else None),
                "year": r.get("pubYear"),
                "source": server.lower(),
            }
        )
        if len(items) >= limit:
            break
    return items


async def search_biorxiv(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return await _search_preprints_via_europepmc(query, limit, "bioRxiv")


async def search_medrxiv(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return await _search_preprints_via_europepmc(query, limit, "medRxiv")


# ── OpenAIRE ─────────────────────────────────────────────────────────────

_OPENAIRE_BASE = "https://api.openaire.eu/search/publications"


def parse_openaire_payload(payload: Any) -> list[dict[str, Any]]:
    """Flatten OpenAIRE's nested JSON into generic dicts (testable offline)."""
    results = (((payload or {}).get("response") or {}).get("results") or {}).get("result") or []
    items: list[dict[str, Any]] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        metadata = entry.get("metadata") or {}
        entity = metadata.get("oaf:entity") or {}
        result = entity.get("oaf:result") or {}
        if not isinstance(result, dict):
            continue

        def _text(node: Any) -> str:
            if isinstance(node, dict):
                return str(node.get("$") or "").strip()
            return str(node or "").strip()

        title = _text(result.get("title"))
        if not title:
            continue
        doi = ""
        url = _text(result.get("url"))
        for pid in result.get("pid") or []:
            if not isinstance(pid, dict):
                continue
            if str(pid.get("@idtype", "")).lower() == "doi" and not doi:
                value = _text(pid)
                if value:
                    doi = value
        date = _text(result.get("dateofacceptance"))
        authors: list[str] = []
        for author in result.get("author") or []:
            if isinstance(author, dict):
                name = _text(author.get("fullname"))
                if name:
                    authors.append(name)
        items.append({"title": title, "doi": doi, "url": url or None, "year": date[:4], "authors": authors})
    return items


async def search_openaire(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """OpenAIRE publications search (no key required)."""
    term = (query or "").strip()
    if not term:
        return []
    client = _client_for(_OPENAIRE_BASE)
    payload = await _get_json(
        client,
        _OPENAIRE_BASE,
        {"keywords": term, "format": "json", "size": max(1, min(limit, 50))},
    )
    return parse_openaire_payload(payload)


# ── CiNii ────────────────────────────────────────────────────────────────

_CINII_BASE = "https://cir.nii.ac.jp/all"


async def search_cinii(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """CiNii Research search (JSON-LD). Best-effort: the endpoint is
    occasionally slow from outside Japan; callers apply deadlines."""
    term = (query or "").strip()
    if not term:
        return []
    client = _client_for(_CINII_BASE)
    resp = await client.get(
        _CINII_BASE,
        params={"q": term, "format": "json", "count": max(1, min(limit, 50))},
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    payload = resp.json()
    graph = payload.get("@graph") or []
    items: list[dict[str, Any]] = []
    for node in graph:
        if not isinstance(node, dict):
            continue
        title = node.get("title")
        if not title:
            continue
        items.append(
            {
                "title": title,
                "url": node.get("@id"),
                "doi": node.get("doi"),
                "year": str(node.get("publicationDate") or "")[:4] or None,
                "authors": [a.get("name") for a in node.get("creator") or [] if isinstance(a, dict)],
                "journal": node.get("periodical"),
            }
        )
    return items


# ── SciELO ───────────────────────────────────────────────────────────────

_SCIELO_BASE = "https://articlemeta.scielo.org.br/api/v1/article/"


async def search_scielo(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """SciELO ArticleMeta search. Best-effort: responses vary and the
    endpoint is sometimes fronted by bot protection; failures degrade
    gracefully into provider-level warnings."""
    term = (query or "").strip()
    if not term:
        return []
    client = _client_for(_SCIELO_BASE)
    resp = await client.get(
        _SCIELO_BASE,
        params={"q": term, "format": "json", "size": max(1, min(limit, 50))},
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        return []
    items: list[dict[str, Any]] = []
    for article in payload:
        if not isinstance(article, dict):
            continue
        titles = article.get("titles") or {}
        title = next((v for v in titles.values() if isinstance(v, str) and v.strip()), "")
        if not title:
            continue
        doi = article.get("doi") or ""
        authors = [
            str(a.get("given_names", "")).strip() + " " + str(a.get("surname", "")).strip()
            for a in article.get("authors") or []
            if isinstance(a, dict)
        ]
        links = article.get("links") or {}
        url = ""
        if isinstance(links, dict):
            url = links.get("pdf") or links.get("html") or ""
        items.append(
            {
                "title": title.strip(),
                "authors": [a.strip() for a in authors if a.strip()],
                "doi": doi,
                "url": url or None,
                "year": str(article.get("publication_date") or "")[:4] or None,
                "journal": article.get("journal_title") or article.get("collection"),
            }
        )
    return items


# ── BASE / CORE (API-key gated aggregators) ──────────────────────────────

_BASE_BASE = "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi"


async def search_base(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """BASE (Bielefeld) search; requires a free API key."""
    key = get_config().aggregator_keys.base_api_key.strip()
    if not key:
        raise ProviderConfigError("base", "no API key configured (set LIT_BASE_API_KEY)")
    term = (query or "").strip()
    if not term:
        return []
    client = _client_for(_BASE_BASE)
    payload = await _get_json(
        client,
        _BASE_BASE,
        {
            "func": "PerformSearch",
            "query": term,
            "format": "json",
            "hits": max(1, min(limit, 100)),
            "key": key,
        },
    )
    docs = ((payload or {}).get("response") or {}).get("docs") or []
    items: list[dict[str, Any]] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        title = doc.get("dctitle") or doc.get("title")
        if not title:
            continue
        items.append(
            {
                "title": title,
                "doi": doc.get("dcidentifier") if str(doc.get("dcidentifier", "")).startswith("10.") else "",
                "url": doc.get("dclink"),
                "year": str(doc.get("dcyear") or "")[:4] or None,
                "authors": [a for a in str(doc.get("dcauthor") or "").split(";") if a.strip()],
            }
        )
    return items


_CORE_BASE = "https://api.core.ac.uk/v3/search/works"


async def search_core(
    query: str,
    limit: int,
    identifiers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """CORE search; requires a free API key."""
    key = get_config().aggregator_keys.core_api_key.strip()
    if not key:
        raise ProviderConfigError("core", "no API key configured (set LIT_CORE_API_KEY)")
    term = (query or "").strip()
    if not term:
        return []
    client = _client_for(_CORE_BASE)
    resp = await client.get(
        _CORE_BASE,
        params={"q": term, "limit": max(1, min(limit, 100))},
        headers={"Authorization": f"Bearer {key}"},
    )
    resp.raise_for_status()
    payload = resp.json()
    items: list[dict[str, Any]] = []
    for work in (payload or {}).get("results") or []:
        if not isinstance(work, dict):
            continue
        title = work.get("title")
        if not title:
            continue
        items.append(
            {
                "title": title,
                "doi": work.get("doi"),
                "url": work.get("url") or work.get("sourceFulltextUrls") or None,
                "year": str(work.get("yearPublished") or "")[:4] or None,
                "authors": [a.get("name") for a in work.get("authors") or [] if isinstance(a, dict)],
            }
        )
    return items


# ── Registry ─────────────────────────────────────────────────────────────

PY_SEARCH_BACKENDS: dict[str, BackendFn] = {
    "crossref": search_crossref,
    "openalex": search_openalex,
    "europepmc": search_europepmc,
    "pmc": search_pmc,
    "doaj": search_doaj,
    "unpaywall": search_unpaywall,
    "arxiv": search_arxiv,
    "biorxiv": search_biorxiv,
    "medrxiv": search_medrxiv,
    "openaire": search_openaire,
    "cinii": search_cinii,
    "scielo": search_scielo,
    "base": search_base,
    "core": search_core,
}

#: Providers whose search is handled by dedicated Python services in the
#: gateway (kept separate because they have richer per-service APIs).
PYTHON_SERVICE_PROVIDERS = {"pubmed", "semantic_scholar", "clinical_trials", "zenodo", "jstage"}


def has_python_search(provider: str) -> bool:
    return provider in PY_SEARCH_BACKENDS or provider in PYTHON_SERVICE_PROVIDERS
