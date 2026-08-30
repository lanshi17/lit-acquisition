"""Tests for the 0.3.0 optimization pass.

Covers: pure-Python provider backends, relevance-aware ranking, health
circuit breaker, retry classification, and the agent-facing response
contract (summary / diagnostics / compact mode).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from lit_acquisition import (
    LitAcquisitionConfig,
    OnlineAcquisitionResponse,
    OnlineAcquisitionRouteInfo,
    OnlineAcquisitionSourceTraceEntry,
    ProviderHealthTracker,
)
from lit_acquisition.algorithms import lexical_relevance, rank_candidates
from lit_acquisition.gateway import (
    _classify_provider_error,
    _is_retryable,
    call_provider_with_retry,
    resolve_oa_url,
)
from lit_acquisition.models import OnlineAcquisitionGatewayRequest, OnlineAcquisitionGatewayResult
from lit_acquisition.net.pool import _split_timeout
from lit_acquisition.normalize import normalize_items, normalize_pmc
from lit_acquisition.orchestration import (
    _dedupe_warnings,
    _finalize_response,
    search_parallel,
)
from lit_acquisition.providers.backends import (
    _parse_jstage_feed,
    parse_arxiv_atom,
    parse_openaire_payload,
    search_unpaywall,
)
from lit_acquisition.providers.errors import ProviderConfigError

# ── Relevance ranking ────────────────────────────────────────────────────


def test_lexical_relevance_token_overlap():
    query = "MECP2 Rett syndrome case report"
    assert lexical_relevance(query, "A case report of MECP2 in Rett syndrome") > 0.7
    assert lexical_relevance(query, "BRCA1 breast cancer cohort study") < 0.3
    assert lexical_relevance("", "anything") == 0.0


def test_lexical_relevance_cjk_bigrams():
    # Unsegmented CJK still matches via character bigrams.
    assert lexical_relevance("蕾特综合征", "一例蕾特综合征病例报告") > 0.5
    assert lexical_relevance("蕾特综合征", "糖尿病研究") == 0.0


def test_rank_candidates_relevance_first():
    cands = [
        {"title": "BRCA1 in breast cancer", "provider": "crossref", "doi": "10.1/a", "year": "2024"},
        {"title": "MECP2 Rett syndrome case report", "provider": "doaj", "year": "2020"},
        {"title": "Unrelated astronomy survey", "provider": "openalex", "doi": "10.1/b", "year": "2025"},
    ]
    ranked = rank_candidates(cands, expected_title="MECP2 Rett syndrome case report")
    assert ranked[0]["provider"] == "doaj"  # best topical match wins despite missing DOI/year


def test_rank_candidates_exact_title_beats_partial():
    cands = [
        {"title": "MECP2 Rett syndrome", "provider": "a"},
        {"title": "MECP2 Rett syndrome case report", "provider": "b"},
    ]
    ranked = rank_candidates(cands, expected_title="MECP2 Rett syndrome case report")
    assert ranked[0]["provider"] == "b"


# ── Provider backends (offline parsing) ──────────────────────────────────

_ARXIV_FEED = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>  A study of\n MECP2 dynamics </title>
    <published>2024-01-02T00:00:00Z</published>
    <author><name>Jane Doe</name></author>
    <author><name>John Roe</name></author>
    <arxiv:doi>10.48550/arXiv.2401.00001</arxiv:doi>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002v1</id>
    <title></title>
    <published>2024-01-03T00:00:00Z</published>
  </entry>
</feed>"""


def test_parse_arxiv_atom():
    items = parse_arxiv_atom(_ARXIV_FEED)
    assert len(items) == 1  # empty-title entry dropped
    item = items[0]
    assert item["title"] == "A study of MECP2 dynamics"
    assert item["authors"] == ["Jane Doe", "John Roe"]
    assert item["doi"] == "10.48550/arXiv.2401.00001"
    assert item["year"] == "2024"
    assert item["url"] == "https://arxiv.org/abs/2401.00001v1"


def test_parse_openaire_payload():
    payload = {
        "response": {
            "results": {
                "result": [
                    {
                        "metadata": {
                            "oaf:entity": {
                                "oaf:result": {
                                    "title": {"$": "Rett syndrome review"},
                                    "pid": [{"@idtype": "doi", "$": "10.1234/xyz"}],
                                    "url": {"$": "https://example.org/p"},
                                    "dateofacceptance": {"$": "2023-05-01"},
                                    "author": [{"fullname": {"$": "Doe J"}}],
                                }
                            }
                        }
                    }
                ]
            }
        }
    }
    items = parse_openaire_payload(payload)
    assert len(items) == 1
    assert items[0]["title"] == "Rett syndrome review"
    assert items[0]["doi"] == "10.1234/xyz"
    assert items[0]["year"] == "2023"
    assert items[0]["authors"] == ["Doe J"]


@pytest.mark.asyncio
async def test_unpaywall_requires_email(monkeypatch):
    from lit_acquisition import config as cfg_mod

    cfg = LitAcquisitionConfig()
    cfg.unpaywall.email = ""
    monkeypatch.setattr(cfg_mod, "_config", cfg)
    with pytest.raises(ProviderConfigError):
        await search_unpaywall("", 5, {"doi": "10.1038/ng.1234"})


@pytest.mark.asyncio
async def test_unpaywall_keyword_query_no_network(monkeypatch):
    # Keyword query without DOI returns [] without touching the network.
    called = {}

    def explode(*a, **k):
        called["yes"] = True

    monkeypatch.setattr("lit_acquisition.providers.backends._client_for", explode)
    out = await search_unpaywall("some keywords", 5, {})
    assert out == []
    assert not called


def test_normalize_generic_fallback_for_unknown_provider():
    items = normalize_items(
        "totally_new_provider",
        [{"title": "A paper", "doi": "10.5/x", "url": "https://e.org", "year": "2021"}],
    )
    assert len(items) == 1
    assert items[0].title == "A paper"
    assert items[0].doi == "10.5/x"


# ── Regression: resolve_oa_url Unpaywall shapes (was returning None) ─────


def _gw(items):
    return OnlineAcquisitionGatewayResult(provider="unpaywall", success=True, items=items, warnings=[])


def test_resolve_oa_url_unpaywall_prefers_pdf():
    rec = {
        "doi": "10.1/x",
        "best_oa_location": {"url": "https://e.org/landing", "url_for_pdf": "https://e.org/p.pdf"},
    }
    assert resolve_oa_url(_gw([rec])) == "https://e.org/p.pdf"


def test_resolve_oa_url_unpaywall_pdf_in_oa_locations():
    # best_oa_location has only a landing page; a direct PDF lives in oa_locations.
    rec = {
        "doi": "10.1/y",
        "best_oa_location": {"url": "https://e.org/landing"},
        "oa_locations": [{"url": "https://e.org/other", "url_for_pdf": "https://e.org/direct.pdf"}],
    }
    assert resolve_oa_url(_gw([rec])) == "https://e.org/direct.pdf"


def test_resolve_oa_url_unpaywall_landing_fallback():
    rec = {"doi": "10.1/z", "best_oa_location": {"url": "https://e.org/landing"}}
    assert resolve_oa_url(_gw([rec])) == "https://e.org/landing"


# ── Regression: PubMed idtype "pmc" was dropped by the normalizer ────────


def test_normalize_pmc_accepts_idtype_pmc():
    item = {
        "uid": "123",
        "title": "t",
        "pubdate": "2024",
        "articleids": [{"idtype": "pmc", "value": "PMC42"}, {"idtype": "doi", "value": "10.1/d"}],
    }
    ni = normalize_pmc(item)
    assert ni.identifiers.get("pmcid") == "PMC42"
    assert ni.doi == "10.1/d"
    assert ni.url == "https://pmc.ncbi.nlm.nih.gov/articles/PMC42/"


def test_normalize_pmc_still_accepts_idtype_pmcid():
    item = {"uid": "1", "title": "t", "articleids": [{"idtype": "pmcid", "value": "PMC7"}]}
    assert normalize_pmc(item).identifiers.get("pmcid") == "PMC7"


# ── Regression: EuropePMC identifier term construction ───────────────────


@pytest.mark.asyncio
async def test_search_europepmc_empty_query_no_network(monkeypatch):
    from lit_acquisition.providers import backends as pp

    def explode(*a, **k):
        raise AssertionError("network should not be touched for an empty query")

    monkeypatch.setattr(pp, "_client_for", explode)
    out = await pp.search_europepmc("", 5, {})
    assert out == []


# ── Error classification / retry policy ──────────────────────────────────


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://example.org")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError("err", request=req, response=resp)


def test_classify_http_429_actionable():
    warning = _classify_provider_error("semantic_scholar", _http_status_error(429))
    assert warning.startswith("PROVIDER_HTTP_429:semantic_scholar")
    assert "LIT_SEMANTIC_SCHOLAR_API_KEY" in warning


def test_classify_timeout_and_network():
    assert _classify_provider_error("crossref", TimeoutError()).startswith("TIMEOUT:crossref")
    assert _classify_provider_error("core", httpx.ConnectError("boom")).startswith("NETWORK_ERROR:core")


def test_retry_policy():
    config_missing = OnlineAcquisitionGatewayResult(
        provider="base", success=False, items=[], warnings=["CONFIG_MISSING:base:no API key configured"]
    )
    not_found = OnlineAcquisitionGatewayResult(
        provider="crossref", success=False, items=[], warnings=["PROVIDER_HTTP_404:crossref:not found"]
    )
    rate_limited = OnlineAcquisitionGatewayResult(
        provider="semantic_scholar", success=False, items=[], warnings=["PROVIDER_HTTP_429:semantic_scholar:slow down"]
    )
    transient = OnlineAcquisitionGatewayResult(
        provider="pmc", success=False, items=[], warnings=["NETWORK_ERROR:pmc:ConnectError: reset"]
    )
    assert _is_retryable(config_missing) is False
    assert _is_retryable(not_found) is False
    assert _is_retryable(rate_limited) is True
    assert _is_retryable(transient) is True


# ── Health tracker circuit breaker ───────────────────────────────────────


def test_circuit_breaker_opens_on_persistent_failure():
    tracker = ProviderHealthTracker(window_seconds=3600, min_samples=3)
    for _ in range(3):
        tracker.record("flaky", success=False, latency_ms=50)
    assert tracker.should_skip("flaky") is True
    tracker.record("flaky", success=True, latency_ms=50)
    tracker.record("flaky", success=True, latency_ms=50)
    tracker.record("flaky", success=True, latency_ms=50)
    assert tracker.should_skip("flaky") is False


def test_circuit_breaker_needs_min_samples():
    tracker = ProviderHealthTracker(window_seconds=3600, min_samples=3)
    tracker.record("new", success=False, latency_ms=50)
    tracker.record("new", success=False, latency_ms=50)
    assert tracker.should_skip("new") is False  # only 2 failures < min_samples


def test_snapshot_reports_stats():
    tracker = ProviderHealthTracker(window_seconds=3600, min_samples=3)
    tracker.record("x", success=True, latency_ms=100)
    snap = tracker.snapshot()
    assert "x" in snap
    assert snap["x"].success_count == 1



def test_circuit_breaker_half_open_after_cooldown(monkeypatch):
    """An open circuit allows a probe once failures age past the cooldown."""
    import lit_acquisition.health as health_mod

    tracker = ProviderHealthTracker(window_seconds=3600, min_samples=3, probe_cooldown=120.0)
    now = 1000.0
    monkeypatch.setattr(health_mod.time, "time", lambda: now)
    for _ in range(3):
        tracker.record("flaky", success=False, latency_ms=50)
    assert tracker.should_skip("flaky") is True

    # Fresh failures: still open.
    assert tracker.should_skip("flaky") is True

    # After the cooldown elapses since the last failure, a probe is allowed.
    now += 121
    assert tracker.should_skip("flaky") is False


def test_health_tracker_prunes_old_records(monkeypatch):
    """record() prunes in place so the stored list cannot grow unbounded."""
    import lit_acquisition.health as health_mod

    tracker = ProviderHealthTracker(window_seconds=10, min_samples=3)
    now = 0.0
    monkeypatch.setattr(health_mod.time, "time", lambda: now)
    for _ in range(100):
        tracker.record("p", success=True, latency_ms=1)
    assert len(tracker._records["p"]) == 100
    now += 11  # outside the window; next record prunes the 100 old entries
    tracker.record("p", success=True, latency_ms=1)
    assert len(tracker._records["p"]) == 1

# ── Agent-facing response contract ───────────────────────────────────────


def _trace(provider, success, items=0, error=None, warnings=None):
    return OnlineAcquisitionSourceTraceEntry(
        provider=provider,
        attempt=1,
        action="search",
        success=success,
        items_count=items,
        downloads_count=0,
        warnings=warnings or [],
        error=error,
    )


def test_finalize_response_summary_and_diagnostics():
    route = OnlineAcquisitionRouteInfo(prefer="api", used="api", reason="parallel_acquisition")
    resp = OnlineAcquisitionResponse(success=True, items=[], route=route)
    traces = [
        _trace("crossref", True, items=9),
        _trace("doaj", True, items=4),
        _trace("base", False, error="config missing", warnings=["CONFIG_MISSING:base:no key"]),
    ]
    out = _finalize_response(resp, started_at=__import__("time").monotonic() - 0.5, source_trace=traces)
    assert out["summary"].startswith("ok:")
    assert "crossref(9)" in out["summary"]
    assert "failed: base" in out["summary"]
    diag = out["diagnostics"]
    assert diag["items_total"] == 0
    statuses = {p["provider"]: p["status"] for p in diag["providers"]}
    assert statuses["crossref"] == "ok"
    assert statuses["base"] == "failed"


def test_finalize_response_compact_drops_bulk_fields():
    route = OnlineAcquisitionRouteInfo(prefer="api", used="api", reason="x")
    resp = OnlineAcquisitionResponse(
        success=True,
        items=[],
        route=route,
        raw={"source_trace": []},
        candidate_links=[{"url": "https://e.org"}],
    )
    out = _finalize_response(resp, started_at=__import__("time").monotonic(), source_trace=[], compact=True)
    assert out["candidate_links"] == []
    assert out["raw"] is None
    assert out["summary"]  # summary survives compaction


def test_dedupe_warnings_preserves_order():
    warnings = ["a", "b", "a", "c", "b"]
    assert _dedupe_warnings(warnings) == ["a", "b", "c"]


# ── Search-phase latency guards ──────────────────────────────────────────


def test_split_timeout_caps_connect():
    t = _split_timeout(30.0)
    assert t.connect == 10.0
    assert t.read == 30.0
    assert t.write == 30.0
    # A shorter provider timeout must never be raised by the connect cap.
    t2 = _split_timeout(5.0)
    assert t2.connect == 5.0
    assert t2.read == 5.0


async def test_retry_budget_skips_retry_after_slow_attempt(monkeypatch):
    from lit_acquisition import gateway as gw

    attempts = {"n": 0}

    async def slow_fail(request):
        attempts["n"] += 1
        await asyncio.sleep(0.3)
        return OnlineAcquisitionGatewayResult(
            provider=request.provider,
            success=False,
            items=[],
            warnings=[f"TIMEOUT:{request.provider}:exceeded 0s deadline"],
        )

    monkeypatch.setattr(gw, "call_provider", slow_fail)
    request = OnlineAcquisitionGatewayRequest(provider="scielo", action="search", timeout=0.5)
    result = await call_provider_with_retry(request)
    assert attempts["n"] == 1  # first attempt burned >= deadline/2 -> no retry
    assert result.success is False


async def test_retry_budget_allows_fast_failures(monkeypatch):
    from lit_acquisition import gateway as gw

    attempts = {"n": 0}

    async def fast_fail(request):
        attempts["n"] += 1
        return OnlineAcquisitionGatewayResult(
            provider=request.provider,
            success=False,
            items=[],
            warnings=["NETWORK_ERROR:x:ConnectError: refused"],
        )

    monkeypatch.setattr(gw, "call_provider", fast_fail)
    request = OnlineAcquisitionGatewayRequest(provider="cinii", action="search", timeout=30.0)
    await call_provider_with_retry(request)
    assert attempts["n"] == 2  # instant failure stays retryable


async def test_search_parallel_phase_timeout_cancels_slow_providers(monkeypatch):
    from lit_acquisition import orchestration as orch

    async def fake_search(provider, query=None, identifiers=None, limit=20, raw=False, params=None, timeout=None):
        if provider == "slow":
            await asyncio.sleep(5.0)
        return OnlineAcquisitionGatewayResult(provider=provider, success=True, items=[], warnings=[])

    monkeypatch.setattr(orch, "search_provider", fake_search)
    plan = [
        {"route": "api", "provider": "fast"},
        {"route": "api", "provider": "slow"},
    ]
    trace: list[OnlineAcquisitionSourceTraceEntry] = []
    warnings: list[str] = []
    start = time.monotonic()
    out = await search_parallel(
        query="q",
        plan=plan,
        concurrency=2,
        candidate_limit=5,
        trace_sink=trace,
        warning_sink=warnings,
        phase_timeout=0.5,
    )
    elapsed = time.monotonic() - start
    assert elapsed < 3.0  # the hung provider did not hold the phase for 5s
    skipped = [e for e in trace if e.error == "phase_timeout"]
    assert [e.provider for e in skipped] == ["slow"]
    assert any(w.startswith("PHASE_TIMEOUT:search:") for w in warnings)
    assert out == []


async def test_search_multilingual_delegates_to_search_parallel(monkeypatch):
    from lit_acquisition import orchestration as orch

    captured: dict[str, Any] = {}

    async def fake_search_parallel(*, query, plan, concurrency, candidate_limit, **kwargs):
        captured["query"] = query
        captured["plan"] = [item["provider"] for item in plan]
        captured["candidate_limit"] = candidate_limit
        return [{"title": "T", "provider": "crossref"}]

    monkeypatch.setattr(orch, "search_parallel", fake_search_parallel)
    out = await orch.search_multilingual(target="ibd", disease="crohn", language="en", candidate_limit=7)
    assert captured["query"] == "ibd crohn case report"
    assert captured["candidate_limit"] == 7
    assert "pubmed" in captured["plan"]  # en plan routed with its language providers
    assert out[0]["title"] == "T"


# ── J-STAGE httpx backend (pyjstage2 replacement) ────────────────────────

_JSTAGE_OK_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xml:lang="ja">
  <result><status>0</status><message/></result>
  <opensearch:totalResults>335</opensearch:totalResults>
  <entry>
    <article_title>
      <en><![CDATA[Mining the Biomarkers]]></en>
      <ja/>
    </article_title>
    <article_link>
      <en>https://www.jstage.jst.go.jp/article/tjem/256/1/256_27/_article</en>
      <ja>https://www.jstage.jst.go.jp/article/tjem/256/1/256_27/_article/-char/ja/</ja>
    </article_link>
    <author><en><name><![CDATA[Xiuying Kuang]]></name></en><ja/></author>
    <material_title><en><![CDATA[The Tohoku Journal of Experimental Medicine]]></en></material_title>
    <prism:issn>0040-8727</prism:issn>
    <prism:eIssn>1349-3329</prism:eIssn>
    <pubyear>2022</pubyear>
    <prism:doi>10.1620/tjem.256.27</prism:doi>
    <link href="https://www.jstage.jst.go.jp/article/tjem/256/1/256_27/_article/-char/ja/"/>
  </entry>
</feed>"""

_JSTAGE_ERR_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xml:lang="ja">
  <result><status>ERR_001</status><message>ERR_001</message></result>
  <opensearch:totalResults/><opensearch:startIndex/><opensearch:itemsPerPage/>
  <entry><title/><link/><id/><updated/></entry>
</feed>"""


def test_parse_jstage_feed_maps_entries():
    items = _parse_jstage_feed(_JSTAGE_OK_FEED)
    assert len(items) == 1
    item = items[0]
    assert item["article_title_en"] == "Mining the Biomarkers"
    assert item["article_title_ja"] == ""
    assert item["material_title_en"] == "The Tohoku Journal of Experimental Medicine"
    assert item["doi"] == "10.1620/tjem.256.27"
    assert item["link"] == "https://www.jstage.jst.go.jp/article/tjem/256/1/256_27/_article"
    assert item["issn"] == "0040-8727"
    assert item["eissn"] == "1349-3329"
    assert item["pubyear"] == "2022"


def test_parse_jstage_feed_error_response_yields_empty():
    """The exact response shape that crashed pyjstage (int(None)) returns []."""
    assert _parse_jstage_feed(_JSTAGE_ERR_FEED) == []


def test_tavily_and_serpapi_accept_timeout():
    """Regression: the call site passed timeout= but the adapters did not accept it."""
    from lit_acquisition.web_search.serpapi_adapter import SerpApiAdapter
    from lit_acquisition.web_search.tavily_adapter import TavilyAdapter

    tavily = TavilyAdapter(api_key="k", timeout=7)
    assert tavily.timeout == 7
    serpapi = SerpApiAdapter(api_key="k", timeout=11)
    assert serpapi.timeout == 11
