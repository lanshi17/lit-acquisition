"""Tests for lit_acquisition contracts and classifiers."""

from lit_acquisition import (
    RESPONSE_SCHEMA_VERSION,
    LiteratureType,
    OnlineAcquisitionItem,
    OnlineAcquisitionRequest,
    OnlineAcquisitionResponse,
    OnlineAcquisitionRouteInfo,
    build_provider_plan,
    classify_item,
    dedupe_candidates,
    rank_candidates,
    response_json_schema,
)

import time

from lit_acquisition.enums import PreferStrategy
from lit_acquisition.orchestration import _finalize_response
from lit_acquisition.models import DownloadResult


def test_online_acquisition_request_defaults():
    """Request has sensible defaults."""
    req = OnlineAcquisitionRequest()
    assert req.action == "search"
    assert req.prefer == "auto"
    assert req.limit == 20
    assert req.language == "auto"
    assert req.identifiers == []


def test_online_acquisition_request_limit_clamped():
    """Limit is clamped to [1, 200]."""
    req = OnlineAcquisitionRequest(limit=0)
    assert req.limit == 1
    req = OnlineAcquisitionRequest(limit=500)
    assert req.limit == 200


def test_online_acquisition_request_identifier_alias():
    """identifier -> identifiers alias works."""
    req = OnlineAcquisitionRequest(identifier="10.1234/test")
    assert req.identifiers == ["10.1234/test"]


def test_online_acquisition_request_text_alias():
    """text -> query alias works."""
    req = OnlineAcquisitionRequest(text="BRCA1 mutation")
    assert req.query == "BRCA1 mutation"


def test_download_result_defaults():
    """DownloadResult has empty defaults."""
    dr = DownloadResult()
    assert dr.file_path is None
    assert dr.source == ""
    assert dr.warnings == []


def test_classify_case_report():
    """Case report keywords are classified correctly."""
    item = OnlineAcquisitionItem(source="crossref", title="A case report of Rett syndrome")
    assert classify_item(item) == LiteratureType.CASE_REPORT


def test_classify_sequencing():
    """Sequencing keywords are classified correctly."""
    item = OnlineAcquisitionItem(source="crossref", title="Whole exome sequencing of patient cohort")
    assert classify_item(item) == LiteratureType.SEQUENCING


def test_classify_functional():
    """Functional study keywords are classified correctly."""
    item = OnlineAcquisitionItem(source="crossref", title="In vitro functional analysis of MECP2 variant")
    assert classify_item(item) == LiteratureType.FUNCTIONAL


def test_classify_review_rejected():
    """Review articles are rejected (None)."""
    item = OnlineAcquisitionItem(source="crossref", title="A systematic review of Rett syndrome")
    assert classify_item(item) is None


def test_classify_thesis_rejected():
    """Thesis documents are rejected (None)."""
    item = OnlineAcquisitionItem(source="crossref", title="PhD thesis on genetic variants")
    assert classify_item(item) is None


def test_classify_multilingual():
    """Classification works for non-English titles."""
    item_zh = OnlineAcquisitionItem(source="crossref", title="蕾特综合征病例报告")
    assert classify_item(item_zh) == LiteratureType.CASE_REPORT

    item_ja = OnlineAcquisitionItem(source="crossref", title="網膜芽細胞腫の症例報告")
    assert classify_item(item_ja) == LiteratureType.CASE_REPORT


def test_build_provider_plan_default():
    """Default (auto) plan includes major providers."""
    plan = build_provider_plan()
    providers = [p["provider"] for p in plan]
    assert "crossref" in providers
    # PubMed E-utilities are a first-class channel in the auto plan
    # (added 2026-08-14 alongside the neural rerank stage).
    assert "pubmed" in providers
    assert "pmc" in providers
    # New providers should be in the auto plan
    assert "semantic_scholar" in providers
    assert "clinical_trials" in providers
    assert "zenodo" in providers


def test_build_provider_plan_ja():
    """Japanese plan prioritizes J-STAGE and CiNii."""
    plan = build_provider_plan(language="ja")
    providers = [p["provider"] for p in plan]
    assert providers[0] == "jstage"
    assert "cinii" in providers


def test_build_provider_plan_en_includes_new_providers():
    """English plan includes Semantic Scholar, ClinicalTrials.gov, and Zenodo."""
    plan = build_provider_plan(language="en")
    providers = [p["provider"] for p in plan]
    assert "semantic_scholar" in providers
    assert "clinical_trials" in providers
    assert "zenodo" in providers
    # Semantic Scholar should be high priority (before preprint servers)
    ss_idx = providers.index("semantic_scholar")
    arxiv_idx = providers.index("arxiv")
    assert ss_idx < arxiv_idx


def test_build_provider_plan_hints():
    """Provider hints reorder the plan."""
    plan = build_provider_plan(provider_hints=["doaj"])
    assert plan[0]["provider"] == "doaj"


def test_dedupe_candidates():
    """Duplicate candidates by DOI are removed."""
    candidates = [
        {"doi": "10.1234/a", "title": "Paper A", "provider": "crossref"},
        {"doi": "10.1234/a", "title": "Paper A (duplicate)", "provider": "openalex"},
        {"doi": "10.1234/b", "title": "Paper B", "provider": "doaj"},
    ]
    result = dedupe_candidates(candidates)
    assert len(result) == 2


def test_rank_candidates_title_match():
    """Exact title match ranks first."""
    candidates = [
        {"title": "Other Paper", "doi": None, "provider": "crossref"},
        {"title": "Target Paper", "doi": "10.1234/target", "provider": "crossref"},
    ]
    result = rank_candidates(candidates, expected_title="Target Paper")
    assert result[0]["title"] == "Target Paper"


# --- Standardized JSON response envelope ---


_REQUIRED_ENVELOPE_KEYS = {
    "success",
    "schema_version",
    "items",
    "downloads",
    "warnings",
    "route",
    "raw",
    "candidate_links",
    "summary",
    "diagnostics",
}


def _envelope(**overrides) -> OnlineAcquisitionResponse:
    kwargs = {"success": True, "route": OnlineAcquisitionRouteInfo(prefer=PreferStrategy.AUTO)}
    kwargs.update(overrides)
    return OnlineAcquisitionResponse(**kwargs)


def test_response_schema_version_default():
    """Every response carries the current schema version."""
    assert _envelope().schema_version == RESPONSE_SCHEMA_VERSION


def test_response_json_schema_structure():
    """Exported schema covers the full envelope and is draft 2020-12."""
    schema = response_json_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == f"lit-acquisition/response/v{RESPONSE_SCHEMA_VERSION}"
    assert set(schema["properties"]) == _REQUIRED_ENVELOPE_KEYS
    # Only fields without defaults are required.
    assert set(schema["required"]) == {"success", "route"}


def test_finalize_response_emits_standardized_envelope():
    """Workflow output passes every envelope key with consistent types."""
    started = time.monotonic()
    out = _finalize_response(_envelope(), started_at=started, source_trace=[], compact=False)
    assert set(out) == _REQUIRED_ENVELOPE_KEYS
    assert out["schema_version"] == RESPONSE_SCHEMA_VERSION
    assert isinstance(out["warnings"], list)
    assert isinstance(out["items"], list)
    assert isinstance(out["diagnostics"], dict) and "elapsed_ms" in out["diagnostics"]
    assert out["summary"]
    # Compact mode drops bulk fields but keeps the envelope shape.
    compact = _finalize_response(_envelope(), started_at=started, source_trace=[], compact=True)
    assert set(compact) == _REQUIRED_ENVELOPE_KEYS
    assert compact["candidate_links"] == [] and compact["raw"] is None


def test_response_round_trips_through_schema_model():
    """A dumped envelope validates back into the model unchanged."""
    out = _finalize_response(_envelope(), started_at=time.monotonic(), source_trace=[], compact=False)
    reparsed = OnlineAcquisitionResponse.model_validate(out)
    assert reparsed.schema_version == RESPONSE_SCHEMA_VERSION
    assert reparsed.model_dump() == out
