"""Provider health tracking with sliding window stats."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _Record:
    timestamp: float
    success: bool
    latency_ms: float


@dataclass
class ProviderStats:
    success_count: int = 0
    failure_count: int = 0
    avg_latency_ms: float = 0.0
    success_rate: float = 1.0


class ProviderHealthTracker:
    """Thread-safe sliding-window health tracker for providers."""

    def __init__(self, window_seconds: int = 3600, min_samples: int = 3):
        self._window = window_seconds
        self._min_samples = min_samples
        self._records: dict[str, list[_Record]] = {}
        self._lock = threading.Lock()

    def record(self, provider: str, *, success: bool, latency_ms: float = 0.0) -> None:
        with self._lock:
            if provider not in self._records:
                self._records[provider] = []
            self._records[provider].append(_Record(timestamp=time.time(), success=success, latency_ms=latency_ms))

    def _prune(self, records: list[_Record]) -> list[_Record]:
        cutoff = time.time() - self._window
        return [r for r in records if r.timestamp >= cutoff]

    def get_stats(self, provider: str) -> ProviderStats:
        with self._lock:
            raw = self._records.get(provider, [])
            records = self._prune(raw)
            if not records:
                return ProviderStats()
            successes = sum(1 for r in records if r.success)
            failures = len(records) - successes
            avg_latency = sum(r.latency_ms for r in records) / len(records)
            return ProviderStats(
                success_count=successes,
                failure_count=failures,
                avg_latency_ms=avg_latency,
                success_rate=successes / len(records) if records else 1.0,
            )

    def reorder_plan(self, plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Reorder provider plan: deprioritize unhealthy providers."""

        def _sort_key(item: dict[str, Any]) -> tuple[int, float]:
            stats = self.get_stats(item["provider"])
            # Unhealthy (high failure) goes to end
            is_unhealthy = int(stats.failure_count >= self._min_samples and stats.success_rate < 0.5)
            return (is_unhealthy, -stats.success_rate)

        return sorted(plan, key=_sort_key)


_singleton: ProviderHealthTracker | None = None
_singleton_lock = threading.Lock()


def get_health_tracker() -> ProviderHealthTracker:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ProviderHealthTracker()
    return _singleton
