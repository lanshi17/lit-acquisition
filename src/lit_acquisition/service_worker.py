"""JSON-lines RPC worker backing the axum HTTP service.

The Rust service in ``server/`` spawns one worker per concurrency slot and
talks to it over stdin/stdout, one JSON object per line. Keeping the Python
runtime in a separate process means the HTTP layer can restart crashed
workers, bound slow searches with a timeout, and avoid linking CPython.

Protocol::

    request   {"id": "1", "method": "search", "params": {...}}
    response  {"id": "1", "ok": true, "result": {...}}
              {"id": "1", "ok": false, "error": {"code": "...", "message": "..."}}

Methods are ``ping``, ``version`` and ``search``. ``search`` runs the same
acquisition workflow as the agent entry point and returns the versioned
``OnlineAcquisitionResponse`` envelope (compact mode: no candidate links or
raw payloads).

Stdout carries the protocol, so loguru's default handler is removed and all
logging is pinned to stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, TextIO

from loguru import logger

from . import __version__, aclose_all
from .models import RESPONSE_SCHEMA_VERSION
from .orchestration import (
    multilingual_acquisition_workflow,
    online_acquisition_workflow,
)

# loguru writes to stderr by default; make it explicit so a stray handler can
# never corrupt the JSON-lines protocol on stdout.
logger.remove()
logger.add(
    sys.stderr,
    level=os.getenv("LIT_WORKER_LOG_LEVEL", "INFO"),
    format="<level>{level: <8}</level> | {message}",
)

MAX_QUERY_CHARS = 512
MAX_LIMIT = 50
VALID_LANGUAGES = {"auto", "multilingual"}
VALID_LITERATURE_TYPES = {"case_report", "sequencing", "functional"}


class RpcError(Exception):
    """Protocol-level error returned to the Rust client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _error(request_id: str, code: str, message: str) -> dict[str, Any]:
    return {"id": request_id, "ok": False, "error": {"code": code, "message": message}}


def _validate_search(params: dict[str, Any]) -> dict[str, Any]:
    query = str(params.get("query") or "").strip()
    if not query:
        raise RpcError("invalid_request", "query must be a non-empty string")
    if len(query) > MAX_QUERY_CHARS:
        raise RpcError("invalid_request", f"query must be at most {MAX_QUERY_CHARS} characters")

    raw_limit = params.get("limit")
    try:
        limit = int(10 if raw_limit is None else raw_limit)
    except (TypeError, ValueError) as exc:
        raise RpcError("invalid_request", "limit must be an integer") from exc
    if not 1 <= limit <= MAX_LIMIT:
        raise RpcError("invalid_request", f"limit must be between 1 and {MAX_LIMIT}")

    language = str(params.get("language") or "auto").strip().lower()
    if language not in VALID_LANGUAGES:
        raise RpcError("invalid_request", "language must be one of: auto, multilingual")

    provider = params.get("provider")
    if provider is not None:
        provider = str(provider).strip().lower() or None

    raw_types = params.get("literature_types") or []
    if not isinstance(raw_types, list):
        raise RpcError("invalid_request", "literature_types must be a list")
    literature_types: list[str] = []
    for raw in raw_types:
        value = str(raw).strip().lower()
        if value not in VALID_LITERATURE_TYPES:
            raise RpcError(
                "invalid_request",
                f"unsupported literature_type {value!r}; expected case_report, sequencing, functional",
            )
        if value not in literature_types:
            literature_types.append(value)

    return {
        "query": query,
        "limit": limit,
        "language": language,
        "provider": provider,
        "literature_types": literature_types,
    }


def _truncate(result: dict[str, Any], limit: int) -> dict[str, Any]:
    """Cap the returned items at ``limit``.

    The workflow's own ``limit`` is a per-provider hint, so a federated search
    can legitimately return more items than a caller asked for. HTTP clients
    expect ``limit`` to mean "at most this many results", which is also how
    ``search_literature`` behaves, so the worker applies it here and keeps
    ``diagnostics.items_total`` consistent with the returned list.
    """
    items = result.get("items")
    if not isinstance(items, list) or len(items) <= limit:
        return result
    result["items"] = items[:limit]
    diagnostics = result.get("diagnostics")
    if isinstance(diagnostics, dict) and "items_total" in diagnostics:
        diagnostics["items_total"] = len(result["items"])
    return result


async def _search(params: dict[str, Any]) -> dict[str, Any]:
    request = _validate_search(params)
    payload: dict[str, Any] = {
        "query": request["query"],
        "action": "search",
        "prefer": "api",
        "limit": request["limit"],
        "compact": True,
        "relevance_gate": False,
        "literature_types": request["literature_types"],
    }
    if request["provider"]:
        payload["api_provider"] = request["provider"]

    if request["language"] == "multilingual":
        result = await multilingual_acquisition_workflow(payload)
    else:
        result = await online_acquisition_workflow(payload)
    return _truncate(result, request["limit"])


async def _dispatch(method: str, params: dict[str, Any]) -> Any:
    if method == "ping":
        return {"pong": True}
    if method == "version":
        return {
            "package_version": __version__,
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "python": sys.version.split()[0],
        }
    if method == "search":
        return await _search(params)
    raise RpcError("method_not_found", f"unknown method: {method!r}")


async def _serve(stdin: TextIO, stdout: TextIO, once: bool = False) -> None:
    try:
        while True:
            line = stdin.readline()
            if not line:
                break
            if not line.strip():
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                response = _error("", "invalid_json", str(exc))
            else:
                request_id = str(message.get("id", "")) if isinstance(message, dict) else ""
                try:
                    if not isinstance(message, dict):
                        raise RpcError("invalid_request", "request must be a JSON object")
                    method = str(message.get("method") or "")
                    params = message.get("params") or {}
                    if not isinstance(params, dict):
                        raise RpcError("invalid_request", "params must be a JSON object")
                    result = await _dispatch(method, params)
                    response = {"id": request_id, "ok": True, "result": result}
                except RpcError as exc:
                    response = _error(request_id, exc.code, exc.message)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # keep the worker alive; report, do not crash
                    logger.exception("worker request failed")
                    response = _error(request_id, "internal_error", f"{type(exc).__name__}: {exc}")

            stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
            stdout.flush()
            if once:
                break
    finally:
        try:
            await aclose_all()
        except Exception:  # pragma: no cover - best-effort cleanup
            logger.debug("failed to close shared HTTP clients", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="lit-acquisition JSON-lines worker")
    parser.add_argument("--once", action="store_true", help="handle a single request, then exit")
    args = parser.parse_args(argv)

    try:
        asyncio.run(_serve(sys.stdin, sys.stdout, once=args.once))
    except KeyboardInterrupt:
        logger.info("worker interrupted")
    return 0


# The console entry point is exercised for real by
# tests/test_service_worker.py::test_worker_process_round_trip, which runs
# this module in a subprocess; line coverage cannot observe that child.
if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
