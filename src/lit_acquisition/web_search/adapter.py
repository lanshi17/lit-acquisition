"""Abstract web search adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchLink:
    """A single candidate download link discovered by web search."""

    url: str
    source: str | None = None
    title: str | None = None
    doi: str | None = None


@dataclass
class WebSearchResult:
    """Result container from a web search adapter."""

    links: list[SearchLink] = field(default_factory=list)
    query: str = ""
    provider: str = ""
    warnings: list[str] = field(default_factory=list)


class WebSearchAdapter(ABC):
    """Abstract base for web search backends.

    Implementations must provide ``search`` to discover candidate download
    links from a query string.  The adapter pattern allows swapping the
    underlying search engine (Firecrawl, Serper, Tavily, etc.) without
    changing downstream orchestration.
    """

    def __init__(self, *, api_key: str, base_url: str = "", timeout: int = 30, max_results: int = 10) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_results = max_results

    @abstractmethod
    async def search(self, query: str, *, language: str | None = None) -> WebSearchResult:
        """Search the web for candidate download links.

        Args:
            query: Search query (literature title, keywords, etc.).
            language: Optional language hint (ISO 639-1 code).

        Returns:
            WebSearchResult with candidate links.
        """

    @abstractmethod
    async def scrape_links(self, url: str) -> list[SearchLink]:
        """Scrape a page for PDF/download links.

        Args:
            url: The page URL to scrape.

        Returns:
            List of discovered download links.
        """
