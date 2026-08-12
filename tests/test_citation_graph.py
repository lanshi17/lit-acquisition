"""Tests for citation graph traversal module."""

from unittest.mock import AsyncMock

import pytest

from lit_acquisition.citation_graph import traverse_citation_graph
from lit_acquisition.semantic_scholar import _RELATED_FIELDS


def test_related_fields_exclude_tldr():
    """Citations/references endpoints reject `tldr` (HTTP 400); default fields must omit it.

    Regression test: requesting `tldr` on the citations endpoint returns
    "Unrecognized or unsupported fields: [tldr]", breaking citation traversal.
    """
    assert "tldr" not in _RELATED_FIELDS
    assert "title" in _RELATED_FIELDS
    assert "citationCount" in _RELATED_FIELDS


@pytest.mark.asyncio
async def test_traverse_citation_graph_resolves_doi():
    """Citation graph traversal resolves a DOI seed and fetches related papers."""

    mock_service = AsyncMock()
    # _resolve_paper_id returns a Semantic Scholar paperId
    mock_service._resolve_paper_id = AsyncMock(return_value="abc123def456")
    # get_citations returns papers that cite the seed
    mock_service.get_citations = AsyncMock(return_value=[
        {
            "paperId": "cite1",
            "title": "Paper citing the seed",
            "citationCount": 10,
            "externalIds": {"DOI": "10.1/cite1"},
        },
        {
            "paperId": "cite2",
            "title": "Another citing paper",
            "citationCount": 5,
            "externalIds": {"DOI": "10.1/cite2"},
        },
    ])
    # get_references returns papers cited by the seed
    mock_service.get_references = AsyncMock(return_value=[
        {
            "paperId": "ref1",
            "title": "Paper cited by seed",
            "citationCount": 50,
            "externalIds": {"DOI": "10.1/ref1"},
        },
    ])

    results = await traverse_citation_graph(
        seed="10.1038/ng.1234",
        max_depth=1,
        max_papers=50,
        direction="both",
        service=mock_service,
    )

    assert len(results) == 3
    # Sorted by citation count descending
    assert results[0]["paperId"] == "ref1"  # citationCount=50
    assert results[1]["paperId"] == "cite1"  # citationCount=10
    assert results[2]["paperId"] == "cite2"  # citationCount=5


@pytest.mark.asyncio
async def test_traverse_citation_graph_citations_only():
    """direction='citations' only fetches citing papers."""

    mock_service = AsyncMock()
    mock_service._resolve_paper_id = AsyncMock(return_value="abc123")
    mock_service.get_citations = AsyncMock(return_value=[
        {"paperId": "c1", "title": "Citer", "citationCount": 1},
    ])
    mock_service.get_references = AsyncMock(return_value=[
        {"paperId": "r1", "title": "Reference", "citationCount": 100},
    ])

    results = await traverse_citation_graph(
        seed="10.1/test",
        direction="citations",
        service=mock_service,
    )

    assert len(results) == 1
    assert results[0]["paperId"] == "c1"


@pytest.mark.asyncio
async def test_traverse_citation_graph_max_papers():
    """max_papers limits the total results."""

    mock_service = AsyncMock()
    mock_service._resolve_paper_id = AsyncMock(return_value="abc123")
    mock_service.get_citations = AsyncMock(return_value=[
        {"paperId": f"p{i}", "title": f"Paper {i}", "citationCount": i}
        for i in range(10)
    ])
    mock_service.get_references = AsyncMock(return_value=[])

    results = await traverse_citation_graph(
        seed="10.1/test",
        max_papers=3,
        direction="both",
        service=mock_service,
    )

    assert len(results) <= 3


@pytest.mark.asyncio
async def test_traverse_citation_graph_unresolvable_seed():
    """Returns empty list when seed cannot be resolved."""

    mock_service = AsyncMock()
    mock_service._resolve_paper_id = AsyncMock(return_value=None)

    results = await traverse_citation_graph(
        seed="nonexistent",
        service=mock_service,
    )

    assert results == []


@pytest.mark.asyncio
async def test_traverse_citation_graph_deduplicates():
    """Papers appearing in both citations and references are not duplicated."""

    mock_service = AsyncMock()
    mock_service._resolve_paper_id = AsyncMock(return_value="abc123")
    shared_paper = {"paperId": "shared", "title": "Shared", "citationCount": 5}
    mock_service.get_citations = AsyncMock(return_value=[shared_paper])
    mock_service.get_references = AsyncMock(return_value=[shared_paper])

    results = await traverse_citation_graph(
        seed="10.1/test",
        direction="both",
        service=mock_service,
    )

    # Should be deduplicated - only 1 instance
    assert len(results) == 1
    assert results[0]["paperId"] == "shared"
