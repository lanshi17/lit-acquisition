"""Firecrawl-based web search adapter.

Uses Firecrawl's search API for link discovery and JSON-mode scrape for
structured metadata extraction (title, DOI, PDF URLs).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from ..net.security import redact_secrets
from .adapter import SearchLink, WebSearchAdapter, WebSearchResult

# Fallback regex patterns for extracting PDF links from markdown/HTML
_PDF_URL_PATTERN = re.compile(
    r'https?://[^\s"\'<>]+\.pdf(?:[^\s"\'<>]*)',
    re.IGNORECASE,
)
_PDF_LINK_PATTERN = re.compile(
    r'\[([^\]]*)\]\((https?://[^\s"\'<>)]+\.pdf[^\s"\'<>)]*)\)',
    re.IGNORECASE,
)
_HREF_PDF_PATTERN = re.compile(
    r'href=["\']?(https?://[^\s"\'<>"\']+\.pdf[^\s"\'<>"\']*)',
    re.IGNORECASE,
)

# JSON schema for structured extraction from article pages
_SCRAPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": ["string", "null"],
            "description": "Full article title. Return null if not found.",
        },
        "doi": {
            "type": ["string", "null"],
            "description": "DOI identifier (e.g. '10.1234/abcd'). Return null if not found.",
        },
        "pdf_url": {
            "type": ["string", "null"],
            "description": (
                "Direct PDF download URL. Look for links ending in .pdf, "
                "download buttons, or 'full text' links. Return null if not found."
            ),
        },
        "other_links": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Other potential fulltext/PDF download URLs on the page (e.g. alternate mirrors, supplementary PDFs)."
            ),
        },
    },
}


def _to_dict(result: Any) -> dict[str, Any]:
    """Convert SDK response to dict — handles both dict and Pydantic model returns."""
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "dict"):
        return result.dict()
    return {}


def _extract_pdf_links_from_markdown(markdown: str) -> list[str]:
    """Fallback: extract PDF URLs from raw markdown via regex."""
    seen: set[str] = set()
    links: list[str] = []

    for match in _PDF_LINK_PATTERN.finditer(markdown):
        url = match.group(2)
        if url not in seen:
            seen.add(url)
            links.append(url)

    for match in _HREF_PDF_PATTERN.finditer(markdown):
        url = match.group(1)
        if url not in seen:
            seen.add(url)
            links.append(url)

    for match in _PDF_URL_PATTERN.finditer(markdown):
        url = match.group(0)
        if url not in seen:
            seen.add(url)
            links.append(url)

    return links


def _doi_to_url(doi: str) -> str:
    """Convert a DOI to its canonical URL."""
    doi = doi.strip()
    if doi.startswith("http"):
        return doi
    return f"https://doi.org/{doi}"


class FirecrawlAdapter(WebSearchAdapter):
    """Web search adapter using Firecrawl's search + JSON-mode scrape.

    Environment:
        ``WEB_SEARCH_API_KEY`` — Firecrawl API key (``fc-...``).
        ``WEB_SEARCH_BASE_URL`` — optional, defaults to ``https://api.firecrawl.dev``.
    """

    def __init__(self, *, api_key: str, base_url: str = "", timeout: int = 30, max_results: int = 10) -> None:
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout, max_results=max_results)
        self._client = None  # lazy-init

    def _get_client(self):
        """Lazy-init the AsyncFirecrawl client."""
        if self._client is None:
            from firecrawl import AsyncFirecrawl

            kwargs = {"api_key": self.api_key}
            if self.base_url and self.base_url != "https://api.firecrawl.dev":
                kwargs["api_url"] = self.base_url
            self._client = AsyncFirecrawl(**kwargs)
        return self._client

    async def search(self, query: str, *, language: str | None = None) -> WebSearchResult:
        """Search via Firecrawl and return candidate links."""
        warnings: list[str] = []
        all_links: list[SearchLink] = []

        try:
            client = self._get_client()
            raw_result = await asyncio.wait_for(
                client.search(query, limit=self.max_results), timeout=self.timeout
            )
            result = _to_dict(raw_result)

            web_results = result.get("web", [])
            for item in web_results:
                if isinstance(item, dict):
                    url = item.get("url", "")
                    title = item.get("title", "")
                elif hasattr(item, "url"):
                    url = getattr(item, "url", "")
                    title = getattr(item, "title", "")
                else:
                    continue

                if not url:
                    continue
                all_links.append(SearchLink(url=url, source="firecrawl-search", title=title or None))

        except TimeoutError:
            msg = f"firecrawl search timed out after {self.timeout}s"
            logger.warning(msg)
            warnings.append(msg)
        except Exception as exc:
            msg = f"firecrawl search failed: {redact_secrets(str(exc))}"
            logger.warning(msg)
            warnings.append(msg)

        return WebSearchResult(
            links=all_links,
            query=query,
            provider="firecrawl",
            warnings=warnings,
        )

    async def scrape_links(self, url: str) -> list[SearchLink]:
        """Scrape a page for article metadata and PDF links using JSON mode.

        First attempts structured extraction via Firecrawl's JSON mode to get
        title, DOI, and PDF URL. Falls back to markdown + regex extraction if
        JSON mode fails or returns no useful links.
        """
        links: list[SearchLink] = []

        # ── Try JSON mode first ──
        try:
            client = self._get_client()
            raw_result = await asyncio.wait_for(
                client.scrape(
                url,
                formats=[
                    {
                        "type": "json",
                        "schema": _SCRAPE_SCHEMA,
                        "prompt": (
                            "Extract the article title, DOI, and direct PDF download URL "
                            "from this academic/journal page. Also list any other fulltext "
                            "or PDF links found on the page."
                        ),
                    }
                ],
                ),
                timeout=self.timeout,
            )
            result = _to_dict(raw_result)

            json_data = result.get("json") or {}
            if not json_data and isinstance(result.get("data"), dict):
                json_data = result["data"].get("json") or {}

            if json_data:
                title = json_data.get("title") or None
                doi = json_data.get("doi") or None
                pdf_url = json_data.get("pdf_url") or None
                other_links = json_data.get("other_links") or []

                # Primary PDF link
                if pdf_url:
                    links.append(SearchLink(url=pdf_url, source="firecrawl-json", title=title, doi=doi))

                # Additional links
                for extra_url in other_links:
                    if extra_url and extra_url != pdf_url:
                        links.append(SearchLink(url=extra_url, source="firecrawl-json", title=title, doi=doi))

                # If we got a DOI but no PDF URL, add DOI landing page as fallback
                if doi and not pdf_url and not links:
                    links.append(SearchLink(url=_doi_to_url(doi), source="firecrawl-json-doi", title=title, doi=doi))

                if links:
                    logger.debug("firecrawl json scrape: {} links from {}", len(links), url)
                    return links

        except TimeoutError:
            logger.warning("firecrawl json scrape timed out after {}s for {}", self.timeout, url)
        except Exception as exc:
            logger.debug(
                "firecrawl json scrape failed for {}, falling back to markdown: {}",
                url,
                redact_secrets(str(exc)),
            )

        # ── Fallback: markdown + regex ──
        try:
            client = self._get_client()
            raw_result = await asyncio.wait_for(
                client.scrape(url, formats=["markdown"]), timeout=self.timeout
            )
            result = _to_dict(raw_result)

            markdown = result.get("markdown", "")
            if not markdown:
                return links

            for pdf_url in _extract_pdf_links_from_markdown(markdown):
                links.append(SearchLink(url=pdf_url, source="firecrawl-scrape"))

            if links:
                logger.debug("firecrawl markdown fallback: {} links from {}", len(links), url)

        except TimeoutError:
            logger.warning("firecrawl markdown scrape timed out after {}s for {}", self.timeout, url)
        except Exception as exc:
            logger.warning("firecrawl scrape failed for {}: {}", url, redact_secrets(str(exc)))

        return links
