"""Tests for the JSON-lines worker that backs the axum service."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lit_acquisition import __version__, service_worker
from lit_acquisition.models import RESPONSE_SCHEMA_VERSION
from lit_acquisition.service_worker import (
    RpcError,
    _dispatch,
    _serve,
    _truncate,
    _validate_search,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


async def test_ping_and_version() -> None:
    assert await _dispatch("ping", {}) == {"pong": True}
    version = await _dispatch("version", {})
    assert version["package_version"] == __version__
    assert version["schema_version"] == RESPONSE_SCHEMA_VERSION
    assert version["python"].startswith(sys.version.split()[0].split(".")[0])


async def test_unknown_method_is_reported() -> None:
    with pytest.raises(RpcError) as excinfo:
        await _dispatch("delete_everything", {})
    assert excinfo.value.code == "method_not_found"


def test_search_validation_defaults() -> None:
    params = _validate_search({"query": "  MECP2 Rett syndrome  "})
    assert params == {
        "query": "MECP2 Rett syndrome",
        "limit": 10,
        "language": "auto",
        "provider": None,
        "literature_types": [],
    }


@pytest.mark.parametrize(
    "params",
    [
        {"query": ""},
        {"query": "x" * 513},
        {"query": "MECP2", "limit": 0},
        {"query": "MECP2", "limit": 51},
        {"query": "MECP2", "limit": "many"},
        {"query": "MECP2", "language": "klingon"},
        {"query": "MECP2", "literature_types": "case_report"},
        {"query": "MECP2", "literature_types": ["review"]},
    ],
)
def test_search_validation_rejects_bad_input(params: dict) -> None:
    with pytest.raises(RpcError) as excinfo:
        _validate_search(params)
    assert excinfo.value.code == "invalid_request"


def test_worker_process_round_trip() -> None:
    """The module must speak clean JSON on stdout and exit on stdin EOF."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC_ROOT), env.get("PYTHONPATH", "")]))
    requests = "\n".join(
        [
            json.dumps({"id": "1", "method": "ping"}),
            json.dumps({"id": "2", "method": "version"}),
            json.dumps({"id": "3", "method": "nope"}),
            "{not json",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-m", "lit_acquisition.service_worker"],
        input=requests + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=REPO_ROOT,
        check=True,
    )

    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [response["id"] for response in responses] == ["1", "2", "3", ""]
    assert responses[0]["result"] == {"pong": True}
    assert responses[1]["result"]["package_version"] == __version__
    assert responses[2]["error"]["code"] == "method_not_found"
    assert responses[3]["error"]["code"] == "invalid_json"

@pytest.mark.asyncio
async def test_search_uses_compact_versioned_workflow(monkeypatch) -> None:
    captured: dict = {}

    async def fake_workflow(payload: dict) -> dict:
        captured.update(payload)
        return {
            "success": True,
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "items": [],
            "query": payload["query"],
        }

    monkeypatch.setattr(
        "lit_acquisition.service_worker.online_acquisition_workflow", fake_workflow
    )
    result = await _dispatch(
        "search", {"query": "MECP2", "limit": 3, "provider": "PubMed"}
    )

    assert result["schema_version"] == RESPONSE_SCHEMA_VERSION
    assert captured["compact"] is True
    assert captured["relevance_gate"] is False
    assert captured["action"] == "search"
    assert captured["api_provider"] == "pubmed"
    assert captured["limit"] == 3


@pytest.mark.asyncio
async def test_multilingual_search_uses_multilingual_workflow(monkeypatch) -> None:
    called: dict = {}

    async def fake_multilingual(payload: dict) -> dict:
        called["multilingual"] = True
        return {
            "success": True,
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "items": [],
        }

    async def fake_online(payload: dict) -> dict:
        raise AssertionError("online workflow must not be used for multilingual")

    monkeypatch.setattr(
        "lit_acquisition.service_worker.multilingual_acquisition_workflow",
        fake_multilingual,
    )
    monkeypatch.setattr(
        "lit_acquisition.service_worker.online_acquisition_workflow", fake_online
    )

    result = await _dispatch("search", {"query": "MECP2", "language": "multilingual"})
    assert called["multilingual"] is True
    assert result["schema_version"] == RESPONSE_SCHEMA_VERSION

@pytest.mark.asyncio
async def test_search_truncates_to_limit(monkeypatch) -> None:
    async def fake_workflow(payload: dict) -> dict:
        return {
            "success": True,
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "items": [{"rank": index} for index in range(1, 20)],
            "diagnostics": {"items_total": 19, "providers": []},
        }

    monkeypatch.setattr(
        "lit_acquisition.service_worker.online_acquisition_workflow", fake_workflow
    )
    result = await _dispatch("search", {"query": "MECP2", "limit": 3})

    assert len(result["items"]) == 3
    assert result["items"][0]["rank"] == 1
    assert result["diagnostics"]["items_total"] == 3


@pytest.mark.asyncio
async def test_search_keeps_short_result_lists(monkeypatch) -> None:
    async def fake_workflow(payload: dict) -> dict:
        return {
            "success": True,
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "items": [{"rank": 1}],
            "diagnostics": {"items_total": 1},
        }

    monkeypatch.setattr(
        "lit_acquisition.service_worker.online_acquisition_workflow", fake_workflow
    )
    result = await _dispatch("search", {"query": "MECP2", "limit": 10})
    assert len(result["items"]) == 1

def _frames(stdout: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


async def test_serve_frames_each_request_and_survives_bad_input() -> None:
    requests = [
        "",  # blank line: skipped, no frame
        "{not json",
        json.dumps([1, 2, 3]),  # JSON, but not an object
        json.dumps({"id": "4", "method": "ping", "params": "not-an-object"}),
        json.dumps({"id": "5", "method": "nope"}),
        json.dumps({"id": "6", "method": "ping"}),
    ]
    stdin = io.StringIO("\n".join(requests) + "\n")
    stdout = io.StringIO()

    await _serve(stdin, stdout)

    frames = _frames(stdout)
    assert [frame["id"] for frame in frames] == ["", "", "4", "5", "6"]
    assert frames[0]["error"]["code"] == "invalid_json"
    assert frames[1]["error"]["code"] == "invalid_request"
    assert frames[2]["error"]["code"] == "invalid_request"
    assert frames[3]["error"]["code"] == "method_not_found"
    assert frames[4]["ok"] is True
    assert frames[4]["result"] == {"pong": True}


async def test_serve_reports_unexpected_errors_without_dying(monkeypatch) -> None:
    async def boom(method: str, params: dict) -> dict:
        raise ValueError("kaboom")

    monkeypatch.setattr(service_worker, "_dispatch", boom)
    stdin = io.StringIO(json.dumps({"id": "1", "method": "search"}) + "\n")
    stdout = io.StringIO()

    await _serve(stdin, stdout, once=True)

    frames = _frames(stdout)
    assert len(frames) == 1
    assert frames[0]["ok"] is False
    assert frames[0]["error"]["code"] == "internal_error"
    assert "kaboom" in frames[0]["error"]["message"]


async def test_serve_once_stops_after_one_frame() -> None:
    stdin = io.StringIO(
        json.dumps({"id": "1", "method": "ping"})
        + "\n"
        + json.dumps({"id": "2", "method": "ping"})
        + "\n"
    )
    stdout = io.StringIO()

    await _serve(stdin, stdout, once=True)

    frames = _frames(stdout)
    assert len(frames) == 1
    assert frames[0]["id"] == "1"


def test_main_once_reads_stdin_and_writes_stdout(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"id": "9", "method": "ping"}) + "\n"))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    assert main(["--once"]) == 0

    frames = _frames(stdout)
    assert frames == [{"id": "9", "ok": True, "result": {"pong": True}}]


def test_truncate_leaves_non_list_items_alone() -> None:
    result = {"items": None, "diagnostics": {"items_total": 0}}
    assert _truncate(result, 2) is result
    assert result["items"] is None


def test_truncate_without_diagnostics_dict() -> None:
    result = {"items": [1, 2, 3], "diagnostics": "not-a-dict"}
    trimmed = _truncate(result, 1)
    assert trimmed["items"] == [1]
    assert trimmed["diagnostics"] == "not-a-dict"


def test_literature_types_are_deduplicated() -> None:
    params = _validate_search(
        {"query": "MECP2", "literature_types": ["case_report", "Case_Report", "sequencing"]}
    )
    assert params["literature_types"] == ["case_report", "sequencing"]

async def test_serve_propagates_cancellation(monkeypatch) -> None:
    async def cancel(method: str, params: dict) -> dict:
        raise asyncio.CancelledError

    monkeypatch.setattr(service_worker, "_dispatch", cancel)
    stdin = io.StringIO(json.dumps({"id": "1", "method": "ping"}) + "\n")

    with pytest.raises(asyncio.CancelledError):
        await _serve(stdin, io.StringIO())


def test_main_returns_zero_on_keyboard_interrupt(monkeypatch) -> None:
    def interrupted(coro) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(service_worker.asyncio, "run", interrupted)
    assert main([]) == 0
