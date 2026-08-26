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

    def __init__(self, window_seconds: int = 3600, min_samples: int = 3, probe_cooldown: float = 120.0):
        self._window = window_seconds
        self._min_samples = min_samples
        # Half-open: after the circuit has been open this long, allow one
        # probe request through instead of skipping for the full window.
        self._probe_cooldown = probe_cooldown
        self._records: dict[str, list[_Record]] = {}
        self._lock = threading.Lock()

    def record(self, provider: str, *, success: bool, latency_ms: float = 0.0) -> None:
        with self._lock:
            if provider not in self._records:
                self._records[provider] = []
            records = self._records[provider]
            records.append(_Record(timestamp=time.time(), success=success, latency_ms=latency_ms))
            # Prune in place so the stored list cannot grow unbounded over a
            # long-lived process (pruning on read leaves the raw list intact).
            cutoff = time.time() - self._window
            self._records[provider] = [r for r in records if r.timestamp >= cutoff]

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

    def should_skip(self, provider: str) -> bool:
        """Circuit breaker: skip providers that are failing persistently.

        Opens once the provider has at least ``min_samples`` failures in the
        window and a success rate below 30%. A half-open probe is allowed
        once per ``probe_cooldown`` seconds: rather than skipping a healthy-
        again provider for the entire window, one request goes through; its
        result re-closes or re-opens the circuit.
        """
        with self._lock:
            records = self._prune(self._records.get(provider, []))
            if not records:
                return False
            successes = sum(1 for r in records if r.success)
            failures = len(records) - successes
            open_circuit = failures >= self._min_samples and (successes / len(records)) < 0.3
            if not open_circuit:
                return False
            # Half-open: if the most recent *failure* is older than the
            # cooldown, allow one throttled probe through instead of keeping
            # the circuit open for the whole window.
            now = time.time()
            last_failure = max((r.timestamp for r in records if not r.success), default=0.0)
            # Skip while failures are fresher than the cooldown; once they
            # age out, a half-open probe is allowed through.
            return now - last_failure < self._probe_cooldown

    def snapshot(self) -> dict[str, ProviderStats]:
        """Current stats for every provider seen in the window."""
        with self._lock:
            providers = list(self._records.keys())
        return {p: self.get_stats(p) for p in providers}


_singleton: ProviderHealthTracker | None = None
_singleton_lock = threading.Lock()


def get_health_tracker() -> ProviderHealthTracker:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ProviderHealthTracker()
    return _singleton
