"""Zenodo API integration.

Zenodo (CERN-operated open science repository) hosts publications,
datasets, software, and other research outputs. Metadata is CC0;
individual records carry their own licenses (typically CC-BY).

API docs: https://developers.zenodo.org/

The API returns metadata and links to files. Full-text download is
limited to records with open access (``access_right: "open"``).
"""

from __future__ import annotations

from typing import Any

from ..config import get_config
from ..net.pool import get_shared_client, resolve_provider_proxy


class ZenodoService:
    """Async client for the Zenodo REST API."""

    def __init__(self, base_url: str | None = None) -> None:
        cfg = get_config()
        self.base_url = (base_url or cfg.zenodo.base_url).rstrip("/")

    def _proxy(self) -> str | None:
        return get_config().network.proxy or None

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search Zenodo records by query.

        Args:
            query: Search query string.
            limit: Maximum results (capped at 100 by API).

        Returns:
            List of raw record dicts from the ``hits.hits`` array.
        """
        term = (query or "").strip()
        if not term:
            return []
        params = {
            "q": term,
            "size": min(max(1, limit), 100),
            "sort": "mostrecent",
        }
        client = get_shared_client(proxy=resolve_provider_proxy(self.base_url))
        resp = await client.get(f"{self.base_url}/records", params=params)
        resp.raise_for_status()
        payload = resp.json()
        hits = payload.get("hits") or {}
        return list(hits.get("hits") or [])

    async def get_record(self, record_id: str) -> dict[str, Any] | None:
        """Get a single Zenodo record by ID.

        Args:
            record_id: Zenodo record ID (numeric string).

        Returns:
            Record dict or ``None`` if not found.
        """
        rid = (record_id or "").strip()
        if not rid:
            return None
        client = get_shared_client(proxy=resolve_provider_proxy(self.base_url))
        resp = await client.get(f"{self.base_url}/records/{rid}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


_service: ZenodoService | None = None


def get_zenodo_service() -> ZenodoService:
    """Return the process-wide ZenodoService singleton."""
    global _service
    if _service is None:
        _service = ZenodoService()
    return _service
