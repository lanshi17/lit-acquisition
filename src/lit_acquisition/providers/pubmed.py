"""PubMed esearch/esummary/efetch integration (async)."""

from __future__ import annotations

import asyncio
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx

from ..net.pool import get_shared_client, resolve_provider_proxy


def _pooled_eutils_client(base_url: str) -> httpx.AsyncClient:
    """Shared keep-alive client for E-utilities (proxy-bypass aware:
    ``ncbi.nlm.nih.gov`` is in the default no_proxy list)."""
    return get_shared_client(proxy=resolve_provider_proxy(base_url))


async def _request_with_retry(method, url: str, *, params: dict | None = None, max_attempts: int = 2):
    """Wrap an httpx request with retry on transient network errors."""
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await method(url, params=params)
        except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout) as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                await asyncio.sleep(1.0 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


@dataclass
class OnlineAcquisitionPubMedCandidate:
    pmid: str
    pmcid: str = ""
    doi: str = ""
    title: str = ""
    journal: str = ""
    pub_date: str = ""

    @property
    def pmc_pdf_url(self) -> str:
        """Direct PMC PDF URL if PMCID is available."""
        if self.pmcid:
            return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{self.pmcid}/pdf/"
        return ""


@dataclass
class OnlineAcquisitionPubMedArticle:
    pmid: str
    title: str
    journal: str
    pub_date: str
    abstract: str


class OnlineAcquisitionPubMedService:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("PUBMED_BASE_URL", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils")
        ).rstrip("/")
        self.api_key = api_key or os.getenv("PUBMED_API_KEY", "")

    async def search_candidates(
        self,
        query: str,
        candidate_limit: int = 15,
    ) -> list[OnlineAcquisitionPubMedCandidate]:
        """Search PubMed and return candidate list."""
        term = (query or "").strip()
        if not term:
            return []

        # Cap at 100 to bound esummary payload size; the former hard cap of 15
        # silently truncated candidate budgets (benchmark methods requesting
        # 20-50 candidates received at most 15, biasing method comparisons).
        params: dict[str, Any] = {
            "db": "pubmed",
            "term": term,
            "retmax": max(1, min(candidate_limit, 100)),
            "retmode": "json",
            "sort": "pub date",
        }
        if self.api_key:
            params["api_key"] = self.api_key

        client = _pooled_eutils_client(self.base_url)
        esearch_resp = await _request_with_retry(client.get, f"{self.base_url}/esearch.fcgi", params=params)
        esearch_resp.raise_for_status()
        esearch_payload = esearch_resp.json()

        pmids: list[str] = esearch_payload.get("esearchresult", {}).get("idlist", []) or []
        if not pmids:
            return []

        summary_params: dict[str, Any] = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "json",
        }
        if self.api_key:
            summary_params["api_key"] = self.api_key
        summary_resp = await _request_with_retry(client.get, f"{self.base_url}/esummary.fcgi", params=summary_params)
        summary_resp.raise_for_status()
        summary_payload = summary_resp.json()

        records = summary_payload.get("result", {})
        candidates: list[OnlineAcquisitionPubMedCandidate] = []
        for pmid in pmids:
            row = records.get(pmid, {})
            if not row:
                continue
            # Extract PMCID and DOI from articleids
            pmcid = ""
            doi = ""
            for aid in row.get("articleids", []) or []:
                idtype = str(aid.get("idtype", "")).lower()
                value = str(aid.get("value", "")).strip()
                if idtype == "pmc" and not pmcid:
                    pmcid = value
                elif idtype == "doi" and not doi:
                    doi = value
            candidates.append(
                OnlineAcquisitionPubMedCandidate(
                    pmid=pmid,
                    pmcid=pmcid,
                    doi=doi,
                    title=str(row.get("title") or "").strip(),
                    journal=str(row.get("fulljournalname") or row.get("source") or "").strip(),
                    pub_date=str(row.get("pubdate") or "").strip(),
                )
            )
        return candidates

    async def fetch_article(self, pmid: str) -> OnlineAcquisitionPubMedArticle | None:
        """Fetch full article metadata + abstract by PMID."""
        normalized_pmid = str(pmid or "").strip()
        if not normalized_pmid:
            return None

        summary_params: dict[str, Any] = {
            "db": "pubmed",
            "id": normalized_pmid,
            "retmode": "json",
        }
        if self.api_key:
            summary_params["api_key"] = self.api_key

        fetch_params: dict[str, Any] = {
            "db": "pubmed",
            "id": normalized_pmid,
            "retmode": "xml",
        }
        if self.api_key:
            fetch_params["api_key"] = self.api_key

        client = _pooled_eutils_client(self.base_url)
        summary_resp = await _request_with_retry(client.get, f"{self.base_url}/esummary.fcgi", params=summary_params)
        summary_resp.raise_for_status()
        summary_payload = summary_resp.json()

        fetch_resp = await _request_with_retry(
            client.get, f"{self.base_url}/efetch.fcgi", params=fetch_params
        )
        fetch_resp.raise_for_status()
        fetch_xml = fetch_resp.text

        row = summary_payload.get("result", {}).get(normalized_pmid, {})
        if not row:
            return None

        abstract_fragments: list[str] = []
        try:
            root = ET.fromstring(fetch_xml)
            for node in root.findall(".//Abstract/AbstractText"):
                text = "".join(node.itertext()).strip()
                if text:
                    abstract_fragments.append(text)
        except ET.ParseError:
            abstract_fragments = []

        abstract_text = "\n\n".join(abstract_fragments).strip()
        return OnlineAcquisitionPubMedArticle(
            pmid=normalized_pmid,
            title=str(row.get("title") or "").strip(),
            journal=str(row.get("fulljournalname") or row.get("source") or "").strip(),
            pub_date=str(row.get("pubdate") or "").strip(),
            abstract=abstract_text,
        )


_pubmed_service: OnlineAcquisitionPubMedService | None = None


def get_pubmed_service() -> OnlineAcquisitionPubMedService:
    global _pubmed_service
    if _pubmed_service is None:
        from ..config import get_config

        cfg = get_config()
        _pubmed_service = OnlineAcquisitionPubMedService(
            base_url=cfg.pubmed.base_url,
            api_key=cfg.pubmed.api_key,
        )
    return _pubmed_service
