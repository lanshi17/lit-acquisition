"""ClinicalTrials.gov API v2 integration.

ClinicalTrials.gov is operated by the U.S. National Library of Medicine
(NLM). All data is U.S. government work in the public domain - no
copyright restrictions apply.

API docs: https://clinicaltrials.gov/data-api/api

This provider returns clinical study metadata (trial protocols, status,
phases, conditions, interventions). It does not return PDFs - the data
is structured metadata only.

Bot protection: the API fronts TLS-fingerprint filtering that rejects
plain Python HTTP clients with HTTP 403 even with browser-like headers.
When that happens we fall back to the system ``curl`` binary, whose TLS
fingerprint passes the filter. This keeps the provider usable from
data-center environments without adding a dependency.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any
from urllib.parse import urlencode

from ..config import get_config
from ..net.pool import get_shared_client, resolve_provider_proxy

# clinicaltrials.gov fronts its API with bot protection that rejects
# bare HTTP-client user agents with HTTP 403; identify clearly but with
# a conventional browser UA string.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36 lit-acquisition/0.3"
    ),
    "Accept": "application/json",
}


class ClinicalTrialsService:
    """Async client for the ClinicalTrials.gov v2 API."""

    def __init__(self, base_url: str | None = None) -> None:
        cfg = get_config()
        self.base_url = (base_url or cfg.clinical_trials.base_url).rstrip("/")

    def _proxy(self) -> str | None:
        return get_config().network.proxy or None

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search clinical studies by query term.

        Args:
            query: Search term (disease, gene, intervention, etc.).
            limit: Maximum results (capped at 1000 by API).

        Returns:
            List of raw study dicts from the API ``studies`` array.
            Each dict has a ``protocolSection`` with nested modules.
        """
        term = (query or "").strip()
        if not term:
            return []
        params = {
            "query.term": term,
            "pageSize": min(max(1, limit), 1000),
            "format": "json",
        }
        client = get_shared_client(proxy=resolve_provider_proxy(self.base_url))
        url = f"{self.base_url}/studies"
        resp = await client.get(url, params=params, headers=_BROWSER_HEADERS)
        if resp.status_code == 403:
            # TLS-fingerprint bot protection — fall back to system curl.
            payload = await self._curl_json(url, params)
            return list((payload or {}).get("studies") or [])
        resp.raise_for_status()
        payload = resp.json()
        return list(payload.get("studies") or [])

    async def get_study(self, nct_id: str) -> dict[str, Any] | None:
        """Get a single study by NCT ID.

        Args:
            nct_id: NCT identifier (e.g. ``NCT12345678``).

        Returns:
            Study dict or ``None`` if not found.
        """
        nct = (nct_id or "").strip()
        if not nct:
            return None
        client = get_shared_client(proxy=resolve_provider_proxy(self.base_url))
        url = f"{self.base_url}/studies/{nct}"
        resp = await client.get(url, headers=_BROWSER_HEADERS)
        if resp.status_code == 403:
            return await self._curl_json(url, {})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def _curl_json(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """GET a JSON endpoint via the system ``curl`` binary.

        Used as a fallback when ClinicalTrials.gov's TLS-fingerprint
        filter rejects the Python HTTP client with HTTP 403. Builds the
        command as an argument list (no shell), restricts the scheme to
        HTTPS, and SSRF-validates the target as defense in depth. No
        ``-L`` is passed, so curl will not follow redirects.

        Returns ``None`` on HTTP 404 (study does not exist); raises on
        other non-2xx responses so callers do not ingest an error body
        as a study.
        """
        from ..net.security import validate_url_safe

        validate_url_safe(url)  # SSRF guard on the (config-provided) base URL
        curl = shutil.which("curl")
        if not curl:
            raise RuntimeError("HTTP 403 from clinicaltrials.gov and no system curl available for fallback")
        full_url = url + (f"?{urlencode(params)}" if params else "")
        proc = await asyncio.create_subprocess_exec(
            curl,
            "-sS",
            "--fail-early",
            "--max-time",
            "30",
            "--proto",
            "=https",
            "-H",
            "Accept: application/json",
            # Same browser UA as the httpx path - the TLS filter is UA-aware.
            "-H",
            f"User-Agent: {_BROWSER_HEADERS.get('User-Agent', '')}",
            "-w",
            "\n%{http_code}",
            full_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"curl fallback failed ({proc.returncode}): {stderr.decode(errors='replace')[:200]}")
        raw = stdout.decode(errors="replace")
        body, _, status_str = raw.rpartition("\n")
        status = int(status_str.strip() or "0")
        if status == 404:
            return None
        if status >= 400:
            raise RuntimeError(f"curl fallback got HTTP {status}")
        return json.loads(body or "{}")


def _extract_study_fields(study: dict[str, Any]) -> dict[str, Any]:
    """Flatten the nested protocolSection into a simpler dict for normalization.

    The ClinicalTrials.gov v2 API nests data under ``protocolSection``
    with sub-modules. This function extracts the most useful fields into
    a flat structure that the normalizer can consume.
    """
    proto = study.get("protocolSection") or {}

    ident = proto.get("identificationModule") or {}
    status = proto.get("statusModule") or {}
    sponsor = proto.get("sponsorCollaboratorsModule") or {}
    conditions = proto.get("conditionsModule") or {}
    design = proto.get("designModule") or {}
    description = proto.get("descriptionModule") or {}
    outcomes = proto.get("outcomesModule") or {}
    eligibility = proto.get("eligibilityModule") or {}

    lead_sponsor = sponsor.get("leadSponsor") or {}

    primary_outcomes = outcomes.get("primaryOutcomes") or []
    primary_measures = [
        o.get("measure", "") for o in primary_outcomes if isinstance(o, dict)
    ]

    return {
        "nct_id": ident.get("nctId", ""),
        "title": ident.get("briefTitle", ""),
        "official_title": ident.get("officialTitle", ""),
        "overall_status": status.get("overallStatus", ""),
        "start_date": status.get("startDateStruct", {}).get("date", "") if isinstance(status.get("startDateStruct"), dict) else "",
        "completion_date": status.get("completionDateStruct", {}).get("date", "") if isinstance(status.get("completionDateStruct"), dict) else "",
        "lead_sponsor": lead_sponsor.get("name", ""),
        "sponsor_class": lead_sponsor.get("class", ""),
        "conditions": conditions.get("conditions", []),
        "interventions": conditions.get("interventions", []),
        "phases": design.get("phases", []),
        "study_type": design.get("studyType", ""),
        "enrollment": design.get("enrollmentInfo", {}).get("count", "") if isinstance(design.get("enrollmentInfo"), dict) else "",
        "brief_summary": description.get("briefSummary", ""),
        "primary_measures": primary_measures,
        "eligibility_criteria": eligibility.get("eligibilityCriteria", ""),
        "sex": eligibility.get("sex", ""),
        "min_age": eligibility.get("minimumAge", ""),
        "max_age": eligibility.get("maximumAge", ""),
    }


_service: ClinicalTrialsService | None = None


def get_clinical_trials_service() -> ClinicalTrialsService:
    """Return the process-wide ClinicalTrialsService singleton."""
    global _service
    if _service is None:
        _service = ClinicalTrialsService()
    return _service
