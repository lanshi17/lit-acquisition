"""Edition contract tests: lite edition (no openai) vs agent edition.

The lite edition is the base install: ``openai`` is absent, LLM-backed
stages (translation, relevance gate, neural rerank) must degrade to the
pure-heuristic pipeline with explicit warnings instead of leaking
``ImportError``. The agent edition adds ``openai`` (``[agent]`` extra).

Since the dev environment has ``openai`` installed, the lite contract is
exercised in a subprocess whose ``sitecustomize`` blocks the ``openai``
import at the meta-path level.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ── Agent edition (openai installed in the dev environment) ───────────────


def test_agent_edition_llm_available() -> None:
    from lit_acquisition import agent_edition_hint, llm_available

    if not llm_available():
        pytest.skip("lite edition environment (openai not installed)")
    hint = agent_edition_hint()
    assert "lit-acquisition[agent]" in hint


def test_agent_edition_exports_importable() -> None:
    import lit_acquisition

    for name in (
        "LITERATURE_SEARCH_TOOL_SCHEMA",
        "TOOL_NAME",
        "TOOL_DESCRIPTION",
        "TOOL_SCHEMA",
        "search_literature",
        "neural_rerank",
        "rerank_enabled",
        "run_relevance_gate",
        "translate_query",
        "llm_available",
        "agent_edition_hint",
    ):
        assert hasattr(lit_acquisition, name), name


# ── Lite edition (openai blocked in a subprocess) ─────────────────────────

_LITE_PROBE = textwrap.dedent(
    """
    import asyncio

    import lit_acquisition

    assert lit_acquisition.__version__, "version missing"
    assert lit_acquisition.llm_available() is False
    # Agent tool entry stays importable in the lite edition (no openai needed).
    from lit_acquisition import LITERATURE_SEARCH_TOOL_SCHEMA, search_literature  # noqa: F401

    from lit_acquisition import run_relevance_gate, translate_query

    async def main():
        # Gate must skip with an explicit warning, not raise ImportError.
        gate = await run_relevance_gate(query="MECP2", downloads=[{"file_path": "/tmp/never-read.pdf"}])
        assert gate.total == 1 and gate.relevant == 1, (gate.total, gate.relevant)
        assert any("RELEVANCE_GATE_SKIPPED" in w and "agent" in w for w in gate.warnings), gate.warnings

        # Translation must raise the same ValueError class as missing config
        # (orchestration catches it and falls back to single-language search).
        try:
            await translate_query("MECP2 rett syndrome")
        except ValueError as exc:
            assert "agent edition" in str(exc), exc
        else:
            raise AssertionError("translate_query must raise ValueError in the lite edition")

    asyncio.run(main())
    print("LITE_OK")
    """
)

_BLOCKER_SITECUSTOMIZE = textwrap.dedent(
    """
    import importlib.abc
    import sys


    class _OpenAIBlocker(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "openai" or fullname.startswith("openai."):
                raise ImportError(f"{fullname} blocked: simulating lite edition")
            return None


    sys.meta_path.insert(0, _OpenAIBlocker())
    """
)


@pytest.fixture(name="lite_env")
def fixture_lite_env(tmp_path: Path) -> dict[str, str]:
    blocker_dir = tmp_path / "blocker"
    blocker_dir.mkdir()
    (blocker_dir / "sitecustomize.py").write_text(_BLOCKER_SITECUSTOMIZE)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(blocker_dir)
    return env


def test_lite_edition_degrades_without_openai(lite_env: dict[str, str]) -> None:
    proc = subprocess.run(  # noqa: PLW1510
        [sys.executable, "-c", _LITE_PROBE],
        env=lite_env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "LITE_OK" in proc.stdout


def test_lite_edition_openai_really_blocked(lite_env: dict[str, str]) -> None:
    """Guard for the fixture itself: the blocker must actually reject openai."""
    proc = subprocess.run(  # noqa: PLW1510
        [sys.executable, "-c", "import openai"],
        env=lite_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert "blocked" in (proc.stderr or "")
