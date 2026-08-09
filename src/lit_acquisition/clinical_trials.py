"""ClinicalTrials.gov API v2 integration.

ClinicalTrials.gov is operated by the U.S. National Library of Medicine
(NLM). All data is U.S. government work in the public domain - no
copyright restrictions apply.

API docs: https://clinicaltrials.gov/data-api/api

This provider returns clinical study metadata (trial protocols, status,
phases, conditions, interventions). It does not return PDFs - the data
is structured metadata only.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import get_config


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
        async with httpx.AsyncClient(
            timeout=30.0,
            proxy=self._proxy(),
        ) as client:
            resp = await client.get(
                f"{self.base_url}/studies",
                params=params,
            )
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
        async with httpx.AsyncClient(
            timeout=30.0,
            proxy=self._proxy(),
        ) as client:
            resp = await client.get(f"{self.base_url}/studies/{nct}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()


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
