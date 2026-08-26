"""Semantic Scholar Graph API integration.

Semantic Scholar (Allen AI) indexes 200M+ papers across all fields.
The API provides paper metadata, citation/reference graphs, TLDRs, and
open-access PDF links. Free tier: ~100 requests / 5 min without API key.

API docs: https://api.semanticscholar.org/api-docs/

Copyright note: The API returns metadata (titles, authors, abstracts) and
links to full text. It does not host copyrighted PDFs. Open-access PDFs are
linked via the ``openAccessPdf`` field. Users must respect publishers'
terms when accessing full text.
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from ..config import get_config
from ..net.pool import get_shared_client

_DEFAULT_FIELDS = (
    "title",
    "authors",
    "year",
    "externalIds",
    "openAccessPdf",
    "tldr",
    "fieldsOfStudy",
    "citationCount",
    "influentialCitationCount",
    "publicationTypes",
    "publicationDate",
    "journal",
)

# The citations/references endpoints do NOT support the `tldr` field;
# requesting it returns HTTP 400. Use this subset for related-paper queries.
_RELATED_FIELDS = (
    "title",
    "authors",
    "year",
    "externalIds",
    "openAccessPdf",
    "fieldsOfStudy",
    "citationCount",
    "influentialCitationCount",
    "publicationTypes",
    "publicationDate",
    "journal",
)


class SemanticScholarService:
    """Async client for the Semantic Scholar Graph API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        cfg = get_config()
        self.base_url = (base_url or cfg.semantic_scholar.base_url).rstrip("/")
        self.api_key = api_key or cfg.semantic_scholar.api_key

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _proxy(self) -> str | None:
        return get_config().network.proxy or None

    def _client(self) -> httpx.AsyncClient:
        """Pooled keep-alive client (avoids a TLS handshake per call)."""
        return get_shared_client(proxy=self._proxy())

    async def search(
        self,
        query: str,
        limit: int = 20,
        fields: tuple[str, ...] = _DEFAULT_FIELDS,
    ) -> list[dict[str, Any]]:
        """Search papers by keyword query.

        Args:
            query: Search query string.
            limit: Maximum results (capped at 100 by API).
            fields: Tuple of API fields to request.

        Returns:
            List of raw paper dicts from the API ``data`` array.
        """
        term = (query or "").strip()
        if not term:
            return []
        params = {
            "query": term,
            "limit": min(max(1, limit), 100),
            "fields": ",".join(fields),
        }
        client = self._client()
        resp = await client.get(f"{self.base_url}/paper/search", params=params, headers=self._headers())
        resp.raise_for_status()
        payload = resp.json()
        return list(payload.get("data") or [])

    async def get_paper(
        self,
        paper_id: str,
        fields: tuple[str, ...] = _DEFAULT_FIELDS,
    ) -> dict[str, Any] | None:
        """Get a single paper by Semantic Scholar paperId, DOI, or arXiv ID.

        Args:
            paper_id: Paper identifier. Supports raw paperId, ``DOI:xxx``,
                or ``arXiv:xxx`` prefixes. Plain DOIs starting with ``10.``
                are auto-prefixed.
            fields: API fields to request.

        Returns:
            Paper dict or ``None`` if not found.
        """
        pid = _normalize_paper_id(paper_id)
        if not pid:
            return None
        params = {"fields": ",".join(fields)}
        client = self._client()
        resp = await client.get(f"{self.base_url}/paper/{pid}", params=params, headers=self._headers())
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def get_citations(
        self,
        paper_id: str,
        limit: int = 50,
        fields: tuple[str, ...] = _RELATED_FIELDS,
    ) -> list[dict[str, Any]]:
        """Get papers that cite the given paper.

        Args:
            paper_id: Seed paper identifier (paperId, DOI, or arXiv ID).
            limit: Maximum citations to return.
            fields: API fields to request for each citing paper.

        Returns:
            List of citing paper dicts.
        """
        return await self._get_related(paper_id, "citations", "citingPaper", limit, fields)

    async def get_references(
        self,
        paper_id: str,
        limit: int = 50,
        fields: tuple[str, ...] = _RELATED_FIELDS,
    ) -> list[dict[str, Any]]:
        """Get papers referenced by the given paper.

        Args:
            paper_id: Seed paper identifier (paperId, DOI, or arXiv ID).
            limit: Maximum references to return.
            fields: API fields to request for each cited paper.

        Returns:
            List of referenced paper dicts.
        """
        return await self._get_related(paper_id, "references", "citedPaper", limit, fields)


    async def resolve_paper_id(self, identifier: str) -> str | None:
        """Resolve DOI/arXiv ID/title to a usable Semantic Scholar paper ID.

        Public façade over :meth:`_resolve_paper_id` so callers outside
        this module (e.g. citation-graph traversal) don't depend on a
        private method.

        Args:
            identifier: Paper identifier to resolve.

        Returns:
            A string usable in Semantic Scholar API URLs, or ``None``.
        """
        return await self._resolve_paper_id(identifier)

    async def _get_related(
        self,
        paper_id: str,
        endpoint: str,
        wrapper_key: str,
        limit: int,
        fields: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        """Generic citation/reference fetcher.

        Args:
            paper_id: Seed paper identifier.
            endpoint: ``"citations"`` or ``"references"``.
            wrapper_key: ``"citingPaper"`` or ``"citedPaper"``.
            limit: Maximum results.
            fields: API fields to request.

        Returns:
            List of related paper dicts.
        """
        pid = await self._resolve_paper_id(paper_id)
        if not pid:
            return []
        params = {
            "limit": min(max(1, limit), 1000),
            "fields": ",".join(fields),
        }
        client = self._client()
        resp = await client.get(
            f"{self.base_url}/paper/{pid}/{endpoint}",
            params=params,
            headers=self._headers(),
        )
        resp.raise_for_status()
        payload = resp.json()
        return [
            item.get(wrapper_key) or item
            for item in (payload.get("data") or [])
            if isinstance(item, dict)
        ]

    async def _resolve_paper_id(self, identifier: str) -> str | None:
        """Resolve DOI/arXiv ID/title to a usable Semantic Scholar paper ID.

        If *identifier* is already a paperId (40-char hex) or has a ``DOI:``
        / ``arXiv:`` prefix, it is returned as-is. Otherwise a lookup is
        performed via :meth:`get_paper`.

        Args:
            identifier: Paper identifier to resolve.

        Returns:
            A string usable in Semantic Scholar API URLs, or ``None``.
        """
        pid = _normalize_paper_id(identifier)
        if not pid:
            return None
        # Already a paperId or prefixed ID - usable directly
        if pid.startswith(("DOI:", "ARXIV:", "ARXIV:", "PMID:")):
            return pid
        if len(pid) == 40 and all(c in "0123456789abcdef" for c in pid.lower()):
            return pid
        # Try lookup to get paperId
        try:
            paper = await self.get_paper(pid, fields=("paperId",))
            if paper and paper.get("paperId"):
                return paper["paperId"]
        except httpx.HTTPError as exc:
            logger.warning("Failed to resolve paper id '{}': {}", pid, exc)
        return None


def _normalize_paper_id(raw: str) -> str | None:
    """Normalize a paper identifier for the Semantic Scholar API.

    Handles plain DOIs (auto-prefixes ``DOI:``) and strips whitespace.
    """
    pid = (raw or "").strip()
    if not pid:
        return None
    if pid.lower().startswith("doi:"):
        return f"DOI:{pid[4:].strip()}"
    if pid.lower().startswith("arxiv:"):
        return f"ARXIV:{pid[6:].strip()}"
    if pid.lower().startswith("pmid:"):
        return f"PMID:{pid[5:].strip()}"
    if pid.startswith("10."):
        return f"DOI:{pid}"
    return pid


_service: SemanticScholarService | None = None


def get_semantic_scholar_service() -> SemanticScholarService:
    """Return the process-wide SemanticScholarService singleton."""
    global _service
    if _service is None:
        _service = SemanticScholarService()
    return _service
