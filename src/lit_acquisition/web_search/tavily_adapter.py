"""Tavily-based web search adapter.

Uses Tavily's search API for link discovery with built-in content extraction.
Tavily returns cleaned, LLM-ready content snippets alongside URLs, so the
scrape_links step is lighter than Firecrawl's — we extract PDF links from
the content Tavily already provides.
"""

from __future__ import annotations

import asyncio
import re

from loguru import logger

from ..net.security import redact_secrets
from .adapter import SearchLink, WebSearchAdapter, WebSearchResult

# Regex for PDF URLs in content snippets returned by Tavily.
_PDF_URL_PATTERN = re.compile(
    r'https?://[^\s"\'<>]+\.pdf(?:[^\s"\'<>]*)',
    re.IGNORECASE,
)


class TavilyAdapter(WebSearchAdapter):
    """Web search adapter using Tavily's search API.

    Environment:
        ``TAVILY_API_KEY`` — Tavily API key (``tvly-...``).

    Config:
        Passed via ``WebSearchConfig.tavily_api_key`` and
        ``WebSearchConfig.tavily_search_depth``.
    """

    def __init__(
        self,
        *,
        api_key: str,
        search_depth: str = "basic",
        max_results: int = 10,
    ) -> None:
        super().__init__(api_key=api_key, max_results=max_results)
        self._search_depth = search_depth
        self._client = None  # lazy-init

    def _get_client(self):
        """Lazy-init the Tavily client."""
        if self._client is None:
            from tavily import TavilyClient

            self._client = TavilyClient(api_key=self.api_key)
        return self._client

    async def search(self, query: str, *, language: str | None = None) -> WebSearchResult:
        """Search via Tavily and return candidate links.

        Tavily's ``search_depth="advanced"`` costs 2 API credits but returns
        higher-quality results with richer content snippets.  The default
        ``"basic"`` is sufficient for most literature discovery.
        """
        warnings: list[str] = []
        all_links: list[SearchLink] = []

        try:
            client = self._get_client()
            # Tavily SDK is sync - run in executor (bounded by wait_for so a
            # hung SDK call cannot stall the whole acquisition phase).
            loop = asyncio.get_running_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: client.search(
                        query=query,
                        search_depth=self._search_depth,
                        max_results=self.max_results,
                    ),
                ),
                timeout=self.timeout,
            )

            for item in response.get("results", []):
                url = item.get("url", "")
                title = item.get("title", "")
                if not url:
                    continue
                all_links.append(
                    SearchLink(
                        url=url,
                        source="tavily",
                        title=title or None,
                    )
                )

        except TimeoutError:
            msg = f"tavily search timed out after {self.timeout}s"
            logger.warning(msg)
            warnings.append(msg)
        except Exception as exc:
            msg = f"tavily search failed: {redact_secrets(str(exc))}"
            logger.warning(msg)
            warnings.append(msg)

        return WebSearchResult(
            links=all_links,
            query=query,
            provider="tavily",
            warnings=warnings,
        )

    async def scrape_links(self, url: str) -> list[SearchLink]:
        """Extract PDF links from a URL using Tavily's extract API.

        Falls back to an empty list if extraction fails — Tavily search
        results already include content snippets, so this is best-effort.
        """
        links: list[SearchLink] = []

        try:
            client = self._get_client()
            loop = asyncio.get_running_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: client.extract(url)),
                timeout=self.timeout,
            )

            # Tavily extract returns {"results": [{"url": ..., "raw_content": ...}]}
            for result in response.get("results", []):
                content = result.get("raw_content", "")
                for match in _PDF_URL_PATTERN.finditer(content):
                    pdf_url = match.group(0)
                    links.append(SearchLink(url=pdf_url, source="tavily-extract"))

        except TimeoutError:
            logger.warning("tavily extract timed out after {}s for {}", self.timeout, url)
        except Exception as exc:
            logger.debug("tavily extract failed for {}: {}", url, redact_secrets(str(exc)))

        return links
