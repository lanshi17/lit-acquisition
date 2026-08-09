"""Web search adapter package — adapter pattern for pluggable search backends."""

from .adapter import SearchLink, WebSearchAdapter, WebSearchResult
from .serpapi_adapter import SerpApiAdapter
from .tavily_adapter import TavilyAdapter

__all__ = ["SearchLink", "SerpApiAdapter", "TavilyAdapter", "WebSearchAdapter", "WebSearchResult"]
