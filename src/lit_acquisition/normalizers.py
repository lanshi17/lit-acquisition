"""Per-provider normalization to OnlineAcquisitionItem."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from .contracts import OnlineAcquisitionItem

DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
ISSN_PATTERN = re.compile(r"\b\d{4}-\d{3}[\dX]\b", re.IGNORECASE)


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return str(value).strip() or None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _first(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            text = _clean_text(item)
            if text:
                return text
        return None
    return _clean_text(value)


def _normalize_authors(value: Any) -> list[str]:
    if value is None:
        return []
    authors: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = _clean_text(item.get("name"))
                if not name:
                    given = _clean_text(item.get("given") or item.get("first"))
                    family = _clean_text(item.get("family") or item.get("last"))
                    if given and family:
                        name = f"{given} {family}".strip()
                    else:
                        name = given or family
                if name:
                    authors.append(name)
            else:
                text = _clean_text(item)
                if text:
                    authors.append(text)
        return _dedupe(authors)
    if isinstance(value, str):
        if ";" in value:
            parts = [p.strip() for p in value.split(";") if p.strip()]
        elif " and " in value:
            parts = [p.strip() for p in value.split(" and ") if p.strip()]
        else:
            parts = [value.strip()]
        return _dedupe([p for p in parts if p])
    return []


def _extract_year(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        match = re.search(r"(19|20)\d{2}", value)
        return match.group(0) if match else _clean_text(value)
    if isinstance(value, dict):
        for key in ("date-parts", "date_parts", "dateparts"):
            parts = value.get(key)
            if parts and isinstance(parts, list):
                try:
                    year = parts[0][0]
                    if year:
                        return str(year)
                except (IndexError, TypeError):
                    continue
        for key in ("year", "published_year", "pubyear"):
            if key in value:
                return _extract_year(value.get(key))
    if isinstance(value, (list, tuple)):
        for item in value:
            year = _extract_year(item)
            if year:
                return year
    return None


def _extract_links(values: Sequence[Any]) -> list[str]:
    links: list[str] = []
    for value in values:
        if not value:
            continue
        if isinstance(value, str):
            links.append(value.strip())
            continue
        if isinstance(value, dict):
            for key in ("url", "URL", "link", "landing_page_url", "doi_url", "url_for_pdf"):
                if value.get(key):
                    links.append(str(value.get(key)).strip())
    return _dedupe([link for link in links if link])


def _collect_strings(value: Any, limit: int = 5000) -> list[str]:
    collected: list[str] = []
    queue: list[Any] = [value]
    while queue and len(collected) < limit:
        current = queue.pop(0)
        if isinstance(current, dict):
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
        elif isinstance(current, tuple):
            queue.extend(list(current))
        else:
            text = _clean_text(current)
            if text:
                collected.append(text)
    return collected


def _find_first_match(pattern: re.Pattern[str], values: Iterable[str]) -> str | None:
    for value in values:
        match = pattern.search(value)
        if match:
            return match.group(0)
    return None


# --- Per-provider normalizers ---


def normalize_crossref(item: dict[str, Any]) -> OnlineAcquisitionItem:
    title = _first(item.get("title"))
    authors = _normalize_authors(item.get("author") or item.get("authors"))
    journal = _first(item.get("container-title"))
    doi = _clean_text(item.get("DOI") or item.get("doi"))
    url = _clean_text(item.get("URL") or item.get("url"))
    year = _extract_year(
        item.get("issued")
        or item.get("published")
        or item.get("created")
        or item.get("published-online")
        or item.get("published-print")
    )
    language = _clean_text(item.get("language"))
    publisher = _clean_text(item.get("publisher"))
    issn = _dedupe([_clean_text(v) for v in _as_list(item.get("ISSN")) if _clean_text(v)])
    keywords = _dedupe([_clean_text(v) for v in _as_list(item.get("subject")) if _clean_text(v)])
    links = _extract_links([url, item.get("URL"), item.get("url")])
    return OnlineAcquisitionItem(
        source="crossref",
        title=title,
        authors=authors,
        journal=journal,
        year=year,
        doi=doi,
        url=url,
        links=links,
        language=language,
        publisher=publisher,
        issn=issn,
        identifiers={"issn": issn} if issn else {},
        keywords=keywords,
    )


def normalize_unpaywall(item: dict[str, Any]) -> OnlineAcquisitionItem:
    title = _clean_text(item.get("title") or item.get("publication_title"))
    doi = _clean_text(item.get("doi") or item.get("DOI"))
    journal = _clean_text(item.get("journal_name") or item.get("journal"))
    publisher = _clean_text(item.get("publisher"))
    year = _extract_year(item.get("year") or item.get("published_date"))
    authors = _normalize_authors(item.get("authors") or item.get("author"))
    best_oa = item.get("best_oa_location") or {}
    url = _clean_text(item.get("url") or best_oa.get("url") or best_oa.get("landing_page_url") or item.get("doi_url"))
    links = _extract_links([url, item.get("url"), item.get("doi_url"), best_oa.get("url"), best_oa.get("url_for_pdf")])
    return OnlineAcquisitionItem(
        source="unpaywall",
        title=title,
        authors=authors,
        journal=journal,
        year=year,
        doi=doi,
        url=url,
        links=links,
        language=_clean_text(item.get("language")),
        publisher=publisher,
        identifiers={"is_oa": item.get("is_oa")} if "is_oa" in item else {},
        keywords=_dedupe([_clean_text(v) for v in _as_list(item.get("keywords")) if _clean_text(v)]),
    )


def _extract_pmc_articleids(item: dict[str, Any]) -> dict[str, str]:
    """Extract identifiers from PMC esummary articleids format."""
    ids: dict[str, str] = {}
    for entry in _as_list(item.get("articleids")):
        if not isinstance(entry, dict):
            continue
        idtype = entry.get("idtype", "")
        value = _clean_text(entry.get("value"))
        if not value:
            continue
        if idtype == "pmcid":
            ids["pmcid"] = value
        elif idtype == "pmid":
            ids["pmid"] = value
        elif idtype == "doi":
            ids["doi"] = value
    return ids


def _normalize_pmc_esummary_authors(authors: Any) -> list[str]:
    """Handle esummary authors format: [{"name": "Clausen I", "authtype": "Author"}]."""
    if not isinstance(authors, list):
        return []
    names: list[str] = []
    for entry in authors:
        if isinstance(entry, dict):
            name = _clean_text(entry.get("name"))
            if name:
                names.append(name)
        else:
            text = _clean_text(entry)
            if text:
                names.append(text)
    return _dedupe(names)


def normalize_pmc(item: dict[str, Any]) -> OnlineAcquisitionItem:
    # Detect esummary format (has "uid" and "articleids")
    is_esummary = "uid" in item and "articleids" in item

    if is_esummary:
        title = _clean_text(item.get("title"))
        authors = _normalize_pmc_esummary_authors(item.get("authors"))
        journal = _clean_text(item.get("fulljournalname") or item.get("source"))
        year = _extract_year(item.get("pubdate") or item.get("sortdate"))
        article_ids = _extract_pmc_articleids(item)
        doi = article_ids.get("doi")
        pmcid = article_ids.get("pmcid")
        pmid = article_ids.get("pmid")
        identifiers: dict[str, Any] = {}
        if pmcid:
            identifiers["pmcid"] = pmcid
        if pmid:
            identifiers["pmid"] = pmid
        url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/" if pmcid else None
        return OnlineAcquisitionItem(
            source="pmc",
            title=title,
            authors=authors,
            journal=journal,
            year=year,
            doi=_clean_text(doi),
            url=url,
            links=[url] if url else [],
            language=None,
            issn=[],
            identifiers=identifiers,
            keywords=[],
        )

    # Fallback: generic format
    strings = _collect_strings(item)
    title = _first(item.get("title")) or _find_first_match(re.compile(r"<title>(.+?)</title>"), strings)
    doi = _find_first_match(DOI_PATTERN, strings)
    pmcid = _find_first_match(re.compile(r"\bPMC\d+\b", re.IGNORECASE), strings)
    journal = _first(item.get("journal_title")) or _first(item.get("journal"))
    year = _extract_year(item.get("year") or item.get("pubyear"))
    issn_matches: list[str] = []
    for s in strings:
        issn_matches.extend(ISSN_PATTERN.findall(s))
    issn = _dedupe([m.upper() for m in issn_matches])
    links = _extract_links([item.get("link"), item.get("url")])
    identifiers = {}
    if pmcid:
        identifiers["pmcid"] = pmcid
    return OnlineAcquisitionItem(
        source="pmc",
        title=_clean_text(title),
        authors=_normalize_authors(item.get("authors")),
        journal=_clean_text(journal),
        year=year,
        doi=_clean_text(doi),
        url=_first(links) if links else None,
        links=links,
        language=_clean_text(item.get("language")),
        issn=issn,
        identifiers=identifiers,
        keywords=_dedupe([_clean_text(v) for v in _as_list(item.get("keywords")) if _clean_text(v)]),
    )


def normalize_jstage(item: dict[str, Any]) -> OnlineAcquisitionItem:
    title = _clean_text(item.get("article_title_en") or item.get("article_title_ja"))
    journal = _clean_text(item.get("material_title_en") or item.get("material_title_ja"))
    doi = _clean_text(item.get("doi"))
    url = _clean_text(item.get("link"))
    issn = _dedupe([_clean_text(item.get("issn")), _clean_text(item.get("eissn"))])
    return OnlineAcquisitionItem(
        source="jstage",
        title=title,
        authors=[],
        journal=journal,
        year=_extract_year(item.get("pubyear")),
        doi=doi,
        url=url,
        links=_extract_links([url]),
        language="ja" if item.get("article_title_ja") else None,
        issn=issn,
        identifiers={"issn": issn} if issn else {},
        keywords=[],
    )


def normalize_doaj(item: dict[str, Any]) -> OnlineAcquisitionItem:
    title = _clean_text(item.get("title"))
    journal = _clean_text(item.get("journal_title"))
    doi = _clean_text(item.get("doi"))
    url = _clean_text(_first(item.get("links")))
    issn = _dedupe([_clean_text(v) for v in _as_list(item.get("issns")) if _clean_text(v)])
    links = _extract_links([url] + _as_list(item.get("links")))
    return OnlineAcquisitionItem(
        source="doaj",
        title=title,
        authors=[],
        journal=journal,
        year=_extract_year(item.get("year")),
        doi=doi,
        url=url,
        links=links,
        language=None,
        publisher=_clean_text(item.get("publisher")),
        issn=issn,
        identifiers={"issn": issn} if issn else {},
        keywords=_dedupe([_clean_text(v) for v in _as_list(item.get("keywords")) if _clean_text(v)]),
    )


def _normalize_openalex_authorships(authorships: Any) -> list[str]:
    """Extract authors from OpenAlex authorships format: [{"author": {"display_name": "..."}}]."""
    if not isinstance(authorships, list):
        return []
    authors: list[str] = []
    for entry in authorships:
        if not isinstance(entry, dict):
            continue
        author = entry.get("author")
        if isinstance(author, dict):
            name = _clean_text(author.get("display_name"))
            if name:
                authors.append(name)
        else:
            name = _clean_text(entry.get("name"))
            if name:
                authors.append(name)
    return _dedupe(authors)


def normalize_openalex(item: dict[str, Any]) -> OnlineAcquisitionItem:
    title = _clean_text(item.get("title"))
    authorships = item.get("authorships")
    if (
        isinstance(authorships, list)
        and authorships
        and isinstance(authorships[0], dict)
        and "author" in authorships[0]
    ):
        authors = _normalize_openalex_authorships(authorships)
    else:
        authors = _normalize_authors(authorships or item.get("authors"))
    primary_location = item.get("primary_location")
    journal = None
    if isinstance(primary_location, dict):
        source = primary_location.get("source")
        if isinstance(source, dict):
            journal = _clean_text(source.get("display_name"))
    doi = _clean_text(item.get("doi"))
    url = _clean_text(item.get("id"))
    year = _extract_year(item.get("publication_year") or item.get("year"))
    language = _clean_text(item.get("language"))
    keywords_raw = item.get("keywords") or []
    keywords = _dedupe(
        [
            _clean_text(v.get("display_name"))
            for v in keywords_raw
            if isinstance(v, dict) and _clean_text(v.get("display_name"))
        ]
    )
    return OnlineAcquisitionItem(
        source="openalex",
        title=title,
        authors=authors,
        journal=journal,
        year=year,
        doi=doi,
        url=url,
        links=_extract_links([url, doi]),
        language=language,
        issn=[],
        identifiers={},
        keywords=keywords,
    )


def normalize_scielo(item: dict[str, Any]) -> OnlineAcquisitionItem:
    title = _clean_text(item.get("title"))
    authors = _normalize_authors(item.get("authors"))
    doi = _clean_text(item.get("doi"))
    url = _clean_text(item.get("url"))
    year = _extract_year(item.get("year"))
    journal = _clean_text(item.get("journal") or item.get("source"))
    return OnlineAcquisitionItem(
        source="scielo",
        title=title,
        authors=authors,
        journal=journal,
        year=year,
        doi=doi,
        url=url,
        links=_extract_links([url]),
        language=_clean_text(item.get("language")),
        identifiers={},
        keywords=[],
    )


def normalize_base(item: dict[str, Any]) -> OnlineAcquisitionItem:
    title = _clean_text(item.get("title"))
    authors = _normalize_authors(item.get("authors"))
    doi = _clean_text(item.get("doi"))
    url = _clean_text(item.get("url"))
    year = _extract_year(item.get("year"))
    journal = _clean_text(item.get("journal") or item.get("source"))
    return OnlineAcquisitionItem(
        source="base",
        title=title,
        authors=authors,
        journal=journal,
        year=year,
        doi=doi,
        url=url,
        links=_extract_links([url]),
        language=_clean_text(item.get("language")),
        identifiers={},
        keywords=[],
    )


def normalize_core(item: dict[str, Any]) -> OnlineAcquisitionItem:
    title = _clean_text(item.get("title"))
    authors = _normalize_authors(item.get("authors"))
    doi = _clean_text(item.get("doi"))
    url = _clean_text(item.get("url"))
    year = _extract_year(item.get("year"))
    journal = _clean_text(item.get("journal") or item.get("source"))
    return OnlineAcquisitionItem(
        source="core",
        title=title,
        authors=authors,
        journal=journal,
        year=year,
        doi=doi,
        url=url,
        links=_extract_links([url]),
        language=_clean_text(item.get("language")),
        identifiers={},
        keywords=[],
    )


def normalize_openaire(item: dict[str, Any]) -> OnlineAcquisitionItem:
    title = _clean_text(item.get("title"))
    authors = _normalize_authors(item.get("authors"))
    doi = _clean_text(item.get("doi"))
    url = _clean_text(item.get("url"))
    year = _extract_year(item.get("year"))
    journal = _clean_text(item.get("journal") or item.get("source"))
    return OnlineAcquisitionItem(
        source="openaire",
        title=title,
        authors=authors,
        journal=journal,
        year=year,
        doi=doi,
        url=url,
        links=_extract_links([url]),
        language=_clean_text(item.get("language")),
        identifiers={},
        keywords=[],
    )


def normalize_preprint(item: dict[str, Any]) -> OnlineAcquisitionItem:
    """Normalize preprint server results (arXiv, bioRxiv, medRxiv)."""
    title = _clean_text(item.get("title"))
    authors = _normalize_authors(item.get("authors"))
    doi = _clean_text(item.get("doi"))
    url = _clean_text(item.get("url"))
    year = _extract_year(item.get("year"))
    source = _clean_text(item.get("source")) or "preprint"
    return OnlineAcquisitionItem(
        source=source,
        title=title,
        authors=authors,
        journal=None,
        year=year,
        doi=doi,
        url=url,
        links=_extract_links([url]),
        language=_clean_text(item.get("language")),
        identifiers={},
        keywords=[],
    )


def normalize_cinii(item: dict[str, Any]) -> OnlineAcquisitionItem:
    """Normalize CiNii Research results."""
    title = _clean_text(item.get("title"))
    authors = _normalize_authors(item.get("authors"))
    doi = _clean_text(item.get("doi"))
    url = _clean_text(item.get("url"))
    year = _extract_year(item.get("year"))
    journal = _clean_text(item.get("journal") or item.get("source"))
    return OnlineAcquisitionItem(
        source="cinii",
        title=title,
        authors=authors,
        journal=journal,
        year=year,
        doi=doi,
        url=url,
        links=_extract_links([url]),
        language=_clean_text(item.get("language")),
        identifiers={},
        keywords=[],
    )


def normalize_europepmc(item: dict[str, Any]) -> OnlineAcquisitionItem:
    title = _clean_text(item.get("title") or item.get("articleTitle"))
    author_list = item.get("authorList")
    if isinstance(author_list, dict):
        authors = _normalize_authors(author_list.get("author"))
    else:
        authors = _normalize_authors(item.get("authors"))
    journal = _clean_text(item.get("journalTitle") or item.get("journal"))
    doi = _clean_text(item.get("doi"))
    pmcid = _clean_text(item.get("pmcid"))
    full_text_url_list = item.get("fullTextUrlList")
    url = None
    if isinstance(full_text_url_list, dict):
        ft_urls = full_text_url_list.get("fullTextUrl") or []
        if ft_urls and isinstance(ft_urls, list):
            url = _clean_text(ft_urls[0].get("url"))
    if not url:
        url = _clean_text(item.get("url"))
    year = _extract_year(item.get("pubYear") or item.get("year"))
    identifiers: dict[str, Any] = {}
    if pmcid:
        identifiers["pmcid"] = pmcid
    return OnlineAcquisitionItem(
        source="europepmc",
        title=title,
        authors=authors,
        journal=journal,
        year=year,
        doi=doi,
        url=url,
        links=_extract_links([url]),
        language=_clean_text(item.get("language")),
        issn=[],
        identifiers=identifiers,
        keywords=_dedupe([_clean_text(v) for v in _as_list(item.get("keywords")) if _clean_text(v)]),
    )


def normalize_web_generic(item: dict[str, Any]) -> OnlineAcquisitionItem:
    """Normalize generic web provider result (pubscholar/cyberleninka/hans)."""
    title = _clean_text(item.get("title"))
    authors_raw = item.get("authors")
    if isinstance(authors_raw, str):
        if "," in authors_raw:
            authors = [a.strip() for a in authors_raw.split(",") if a.strip()]
        elif ";" in authors_raw:
            authors = [a.strip() for a in authors_raw.split(";") if a.strip()]
        else:
            authors = [authors_raw.strip()] if authors_raw.strip() else []
    elif isinstance(authors_raw, list):
        authors = [_clean_text(a) or "" for a in authors_raw]
        authors = [a for a in authors if a]
    else:
        authors = []

    url = _clean_text(item.get("detail_link") or item.get("source_link"))
    return OnlineAcquisitionItem(
        source=_clean_text(item.get("source")) or "web",
        title=title,
        authors=authors,
        journal=_clean_text(item.get("journal")),
        year=_extract_year(item.get("year")),
        doi=_clean_text(item.get("doi")),
        url=url,
        links=_extract_links([url]) if url else [],
        language=_clean_text(item.get("language")),
        publisher=_clean_text(item.get("publisher")),
        identifiers={},
        keywords=_dedupe(
            [_clean_text(v) for v in _as_list(item.get("subjects") or item.get("keywords")) if _clean_text(v)]
        ),
    )


def normalize_firecrawl(item: dict[str, Any]) -> OnlineAcquisitionItem:
    """Normalize a Firecrawl search result into an OnlineAcquisitionItem."""
    return OnlineAcquisitionItem(
        source="firecrawl",
        title=_clean_text(item.get("title")),
        authors=[],
        journal=None,
        year=None,
        doi=_clean_text(item.get("doi")),
        url=_clean_text(item.get("url")),
        links=[u for u in [item.get("url")] if u],
        language=None,
        publisher=None,
        issn=[],
        identifiers={},
        keywords=[],
        literature_type=None,
    )


# --- Normalizer registry ---

NORMALIZER_MAP: dict[str, Callable[[dict[str, Any]], OnlineAcquisitionItem]] = {
    "crossref": normalize_crossref,
    "unpaywall": normalize_unpaywall,
    "pmc": normalize_pmc,
    "jstage": normalize_jstage,
    "doaj": normalize_doaj,
    "openalex": normalize_openalex,
    "europepmc": normalize_europepmc,
    "pubscholar": normalize_web_generic,
    "cyberleninka": normalize_web_generic,
    "hans_publishers": normalize_web_generic,
    "firecrawl": normalize_firecrawl,
}


def normalize_items(provider: str, items: list[dict[str, Any]]) -> list[OnlineAcquisitionItem]:
    """Normalize raw provider items to OnlineAcquisitionItem list."""
    normalizer = NORMALIZER_MAP.get(provider)
    if not normalizer:
        return []
    output: list[OnlineAcquisitionItem] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        output.append(normalizer(item))
    return output
