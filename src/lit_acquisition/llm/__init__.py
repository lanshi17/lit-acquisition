"""LLM-backed features: query translation, relevance gate, neural rerank.

These all call an OpenAI-compatible chat/rerank endpoint. They are grouped
here because they share the same concern (LLM integration) and are each
optional - the acquisition pipeline degrades gracefully when no LLM is
configured.
"""

from __future__ import annotations

from .gate import RelevanceGateResult, RelevanceJudgment, run_relevance_gate
from .params import resolve_max_tokens
from .rerank import neural_rerank, rerank_enabled
from .translate import TARGET_LANGUAGES, TranslatedQueries, translate_query

__all__ = [
    "TARGET_LANGUAGES",
    "RelevanceGateResult",
    "RelevanceJudgment",
    "TranslatedQueries",
    "neural_rerank",
    "rerank_enabled",
    "resolve_max_tokens",
    "run_relevance_gate",
    "translate_query",
]
