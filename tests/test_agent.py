"""Tests for the agent-facing search contract."""

from __future__ import annotations

import pytest

from lit_acquisition import LITERATURE_SEARCH_TOOL_SCHEMA, search_literature


def test_tool_schema_requires_query():
    function = LITERATURE_SEARCH_TOOL_SCHEMA["function"]
    assert function["name"] == "search_literature"
    assert "query" in function["parameters"]["required"]


@pytest.mark.asyncio
async def test_empty_query_is_rejected():
    result = await search_literature("  ")
    assert result["success"] is False
    assert any("invalid_request" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_zero_results_are_operational_success(monkeypatch):
    async def fake_workflow(payload):
        assert payload["compact"] is True
        return {
            "success": False,
            "items": [],
            "warnings": ["FETCH_NO_RESULT: no candidates from any source"],
            "summary": "failed: 0 items",
            "diagnostics": {"providers": []},
        }

    monkeypatch.setattr("lit_acquisition.agent.online_acquisition_workflow", fake_workflow)
    result = await search_literature("MECP2")
    assert result["success"] is True
    assert result["total"] == 0
    assert any("no_results" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_results_are_compacted_and_ranked(monkeypatch):
    async def fake_workflow(payload):
        assert payload["api_provider"] == "pubmed"
        return {
            "success": True,
            "items": [
                {
                    "title": "A paper",
                    "authors": ["A", "B", "C", "D", "E", "F", "G"],
                    "year": "2024",
                    "source": "pubmed",
                    "doi": "10.1/x",
                }
            ],
            "warnings": [],
            "summary": "ok: 1 items",
            "diagnostics": {"providers": [{"provider": "pubmed", "status": "ok"}]},
        }

    monkeypatch.setattr("lit_acquisition.agent.online_acquisition_workflow", fake_workflow)
    result = await search_literature("MECP2", limit=1, provider="PubMed")
    assert result["success"] is True
    assert result["items"][0]["rank"] == 1
    assert result["items"][0]["authors"] == ["A", "B", "C", "D", "E", "F"]
    assert result["providers"][0]["provider"] == "pubmed"
