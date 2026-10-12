"""Edition/availability probes for LLM-backed features.

The lite edition (base install) ships without ``openai``; the agent edition
(``pip install 'lit-acquisition[agent]'``) adds it. LLM-backed features must
treat "openai not installed" the same as "no LLM configured" and degrade to
the pure-heuristic pipeline instead of leaking an ``ImportError``.
"""

from __future__ import annotations

import importlib.util

_AGENT_EXTRA_HINT = "pip install 'lit-acquisition[agent]'"


def llm_available() -> bool:
    """Whether the OpenAI-compatible client layer is installed.

    Probes the ``openai`` distribution without importing it (heavy cold
    import); safe to call per-request. A broken/partial install is treated
    as unavailable so LLM-backed features degrade to the lite pipeline
    instead of crashing.
    """
    try:
        return importlib.util.find_spec("openai") is not None
    except (ImportError, ValueError):
        return False


def agent_edition_hint() -> str:
    """Install hint for enabling LLM-backed features."""
    return _AGENT_EXTRA_HINT
