"""Agent-facing literature search entry point."""

from __future__ import annotations

from typing import Any

from loguru import logger

from .orchestration import multilingual_acquisition_workflow, online_acquisition_workflow

TOOL_NAME = "search_literature"
TOOL_DESCRIPTION = (
    "Search academic literature across the configured scholarly providers in parallel, "
    "then return a compact, relevance-ranked result with provider diagnostics."
)
TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Disease, gene, variant, drug, or topic."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "language": {
                    "type": "string",
                    "enum": ["auto", "multilingual"],
                    "default": "auto",
                    "description": "Use multilingual only when non-English literature is required.",
                },
                "literature_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["case_report", "sequencing", "functional"]},
                },
                "provider": {
                    "type": "string",
                    "description": "Optional single provider restriction, for example 'pubmed'.",
                },
            },
            "required": ["query"],
        },
    },
}


def _compact_item(rank: int, item: Any) -> dict[str, Any]:
    get = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
    return {
        "rank": rank,
        "title": get("title"),
        "authors": list(get("authors", []) or [])[:6],
        "year": get("year"),
        "journal": get("journal"),
        "doi": get("doi"),
        "url": get("url"),
        "source": get("source"),
        "literature_type": get("literature_type"),
        "license": get("license"),
    }


async def search_literature(
    query: str,
    *,
    limit: int = 10,
    language: str = "auto",
    literature_types: list[str] | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Search literature and return compact results suitable for an agent tool."""
    query = (query or "").strip()
    limit = max(1, min(50, int(limit or 10)))
    if not query:
        return {
            "success": False,
            "query": query,
            "total": 0,
            "items": [],
            "warnings": ["invalid_request: query must be a non-empty string"],
        }

    payload: dict[str, Any] = {
        "query": query,
        "action": "search",
        "prefer": "api",
        "limit": limit,
        "compact": True,
        "relevance_gate": False,
        "literature_types": literature_types or [],
    }
    if provider and provider.strip():
        payload["api_provider"] = provider.strip().lower()

    try:
        multilingual = (language or "auto").strip().lower() == "multilingual"
        result = (
            await multilingual_acquisition_workflow(payload)
            if multilingual
            else await online_acquisition_workflow(payload)
        )
    except Exception as exc:  # the tool boundary must not raise into the agent
        logger.exception("search_literature failed")
        return {
            "success": False,
            "query": query,
            "total": 0,
            "items": [],
            "warnings": [f"search_failed: {exc}"],
        }

    items = result.get("items") or []
    compact = [_compact_item(index, item) for index, item in enumerate(items[:limit], start=1)]
    warnings = list(result.get("warnings") or [])
    failure_markers = ("invalid_request", "internal_error", "api acquisition failed", "web search acquisition failed")
    failed = any(any(marker in warning.lower() for marker in failure_markers) for warning in warnings)
    if not compact and not failed:
        warnings.append(
            "no_results: search completed but no papers matched; try broader terms or language='multilingual'"
        )

    diagnostics = result.get("diagnostics") or {}
    provider_reports = diagnostics.get("providers") or []
    return {
        "success": not failed,
        "query": query,
        "total": len(compact),
        "items": compact,
        "warnings": warnings,
        "summary": result.get("summary", ""),
        "diagnostics": diagnostics,
        "providers": provider_reports,
    }


LITERATURE_SEARCH_TOOL_SCHEMA = TOOL_SCHEMA

__all__ = [
    "LITERATURE_SEARCH_TOOL_SCHEMA",
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
    "TOOL_SCHEMA",
    "search_literature",
]
