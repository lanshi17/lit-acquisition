"""Pure algorithms: relevance scoring, ranking, dedup, planning, classification.

Everything in this package is side-effect free - no I/O, no global state, no
provider calls. Inputs are plain values (strings / dicts / model instances)
and outputs are new values. This keeps the logic unit-testable in isolation
and clearly separated from the data structures (:mod:`models`) and the I/O
layers (:mod:`providers`, :mod:`gateway`, :mod:`orchestration`).
"""

from __future__ import annotations

from .classify import classify_item, classify_items, filter_by_type
from .dedup import (
    build_candidate_id,
    dedupe_candidates,
    normalize_candidate,
)
from .planning import LANG_PROVIDER_MATRIX, build_provider_plan
from .ranking import rank_candidates
from .relevance import lexical_relevance, relevance_tokens

__all__ = [
    "LANG_PROVIDER_MATRIX",
    "build_candidate_id",
    "build_provider_plan",
    "classify_item",
    "classify_items",
    "dedupe_candidates",
    "filter_by_type",
    "lexical_relevance",
    "normalize_candidate",
    "rank_candidates",
    "relevance_tokens",
]
