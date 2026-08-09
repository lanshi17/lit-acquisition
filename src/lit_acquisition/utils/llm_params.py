"""LLM parameter resolution helpers."""

from __future__ import annotations

import math


def resolve_max_tokens(max_tokens: int, percentage: float = 0.5) -> int:
    """Resolve a max_tokens budget from a config value.

    If ``max_tokens`` is 0 or negative, returns 4096 as a safe default.
    Otherwise returns ``max_tokens * percentage`` rounded up, with a
    minimum of 256.

    Args:
        max_tokens: The configured max_tokens value.
        percentage: Fraction of max_tokens to use (0.0–1.0).

    Returns:
        Resolved token budget.
    """
    if max_tokens <= 0:
        return 4096
    result = math.ceil(max_tokens * percentage)
    return max(256, result)
