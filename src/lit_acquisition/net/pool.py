"""Shared async HTTP client pool.

Every provider call used to construct a fresh ``httpx.AsyncClient``, paying a
TCP + TLS handshake per request and discarding keep-alive connections. This
module caches one pooled client per (proxy, event loop) pair so consecutive
provider calls reuse connections.

Clients are keyed by event loop because ``httpcore`` connection state is bound
to the loop that created it; pytest suites and servers that spawn loops get a
fresh client automatically.
"""

from __future__ import annotations

import asyncio
import threading

import httpx

from ..config import get_config

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "lit-acquisition/0.3 (literature acquisition toolkit)",
}

_pool: dict[tuple[int, str | None], httpx.AsyncClient] = {}
_pool_lock = threading.Lock()


def _build_client(proxy: str | None, timeout: float) -> httpx.AsyncClient:
    cfg = get_config().http
    limits = httpx.Limits(
        max_connections=cfg.max_connections,
        max_keepalive_connections=cfg.max_keepalive_connections,
        keepalive_expiry=cfg.keepalive_expiry,
    )
    net_cfg = get_config().network
    return httpx.AsyncClient(
        proxy=proxy,
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        max_redirects=max(0, net_cfg.max_redirects),
        headers=dict(_DEFAULT_HEADERS),
    )


def get_shared_client(proxy: str | None = None, timeout: float | None = None) -> httpx.AsyncClient:
    """Return a pooled ``AsyncClient`` for the current loop/proxy pair.

    Args:
        proxy: Proxy URL (``None`` for direct connections).
        timeout: Request timeout override (defaults to
            ``HttpPoolConfig.provider_timeout``).

    Returns:
        A reusable ``httpx.AsyncClient``.
    """
    cfg = get_config().http
    resolved_timeout = timeout if timeout is not None else cfg.provider_timeout
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
    except RuntimeError:
        loop_id = 0  # no loop yet - client will be created lazily on first use

    key = (loop_id, proxy or None)
    client = _pool.get(key)
    if client is not None and not client.is_closed:
        return client

    with _pool_lock:
        client = _pool.get(key)
        if client is not None and not client.is_closed:
            return client
        # Evict closed/stale entries so the pool does not grow unbounded.
        stale = [k for k, c in _pool.items() if c.is_closed or (k[0] != 0 and k[0] != loop_id)]
        for k in stale:
            _pool.pop(k, None)
        client = _build_client(proxy or None, resolved_timeout)
        _pool[key] = client
        return client


def resolve_provider_proxy(url: str) -> str | None:
    """Proxy to use for a provider URL, honoring the no_proxy bypass list."""
    return get_config().network.resolve_proxy_for_url(url)


async def aclose_all() -> None:
    """Close all pooled clients (for clean shutdown / tests)."""
    with _pool_lock:
        clients = list(_pool.values())
        _pool.clear()
    for client in clients:
        try:
            await client.aclose()
        except Exception:
            pass
