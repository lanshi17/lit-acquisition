"""SerpApi-based web search adapter.

Uses SerpApi's Google Search (or other engines) for link discovery.
SerpApi returns structured search engine results including titles, links,
and snippets — useful for finding academic PDFs via Google Scholar.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from ..net.security import redact_secrets
from .adapter import SearchLink, WebSearchAdapter, WebSearchResult

# Regex for PDF URLs in snippets.
_PDF_URL_PATTERN = re.compile(
    r'https?://[^\s"\'<>]+\.pdf(?:[^\s"\'<>]*)',
    re.IGNORECASE,
)


class SerpApiAdapter(WebSearchAdapter):
    """Web search adapter using SerpApi.

    Supports Google, Google Scholar, Bing, and other search engines
    configured via the ``engine`` parameter.

    Config:
        Passed via ``WebSearchConfig.serpapi_api_key`` and
        ``WebSearchConfig.serpapi_engine``.
    """

    def __init__(
        self,
        *,
        api_key: str,
        engine: str = "google",
        timeout: int = 30,
        max_results: int = 10,
    ) -> None:
        super().__init__(api_key=api_key, timeout=timeout, max_results=max_results)
        self._engine = engine

    def _build_params(self, query: str, language: str | None = None) -> dict[str, Any]:
        """Build SerpApi search parameters."""
        params: dict[str, Any] = {
            "q": query,
            "api_key": self.api_key,
            "engine": self._engine,
            "num": self.max_results,
        }
        # Google Scholar is best for academic queries
        if self._engine == "google_scholar":
            params.pop("num", None)  # Scholar uses different param
        if language:
            params["hl"] = language
        return params

    async def search(self, query: str, *, language: str | None = None) -> WebSearchResult:
        """Search via SerpApi and return candidate links."""
        warnings: list[str] = []
        all_links: list[SearchLink] = []

        try:
            from serpapi import GoogleSearch

            params = self._build_params(query, language)
            loop = asyncio.get_running_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: GoogleSearch(params).get_dict()),
                timeout=self.timeout,
            )

            organic = response.get("organic_results", [])
            seen_urls: set[str] = set()

            # Organic search results (standard Google / Scholar share this key)
            for item in organic:
                url = item.get("link", "")
                title = item.get("title", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                all_links.append(
                    SearchLink(
                        url=url,
                        source=f"serpapi-{self._engine}",
                        title=title or None,
                    )
                )

            # Inline PDF links from result resources (Scholar-specific)
            for item in organic:
                for resource in item.get("resources", []):
                    resource_url = resource.get("link", "")
                    if resource_url and resource_url not in seen_urls:
                        seen_urls.add(resource_url)
                        all_links.append(
                            SearchLink(
                                url=resource_url,
                                source="serpapi-scholar-pdf",
                                title=item.get("title"),
                            )
                        )

        except TimeoutError:
            msg = f"serpapi search timed out after {self.timeout}s"
            logger.warning(msg)
            warnings.append(msg)
        except Exception as exc:
            msg = f"serpapi search failed: {redact_secrets(str(exc))}"
            logger.warning(msg)
            warnings.append(msg)

        return WebSearchResult(
            links=all_links,
            query=query,
            provider=f"serpapi-{self._engine}",
            warnings=warnings,
        )

    async def scrape_links(self, url: str) -> list[SearchLink]:
        """Extract PDF links from a URL using SerpApi's page scraping.

        SerpApi doesn't have a direct scrape endpoint, so this is a no-op.
        The search results already include enough metadata for most cases.
        For deeper scraping, use Firecrawl or Tavily as the primary adapter.
        """
        return []
