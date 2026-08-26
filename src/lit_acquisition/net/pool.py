"""Shared async HTTP client pool.

Every provider call used to construct a fresh ``httpx.AsyncClient``, paying a
TCP + TLS handshake per request and discarding keep-alive connections. This
module caches one pooled client per (proxy, timeout, event loop) tuple so
consecutive provider calls reuse connections.

Clients are keyed by event loop because ``httpcore`` connection state is bound
to the loop that created it; pytest suites and servers that spawn loops get a
fresh client automatically.
"""

from __future__ import annotations

import asyncio
import threading

import httpx
from loguru import logger

from ..config import get_config

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "lit-acquisition/0.3 (literature acquisition toolkit)",
}

# Pooled entry: the client plus the event loop it was built on. The loop is
# needed to close the client safely when the entry is evicted by a call
# running on a different loop/thread.
_pool: dict[tuple[int, str | None, float], tuple[httpx.AsyncClient, asyncio.AbstractEventLoop | None]] = {}
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
        # Proxy routing is explicit configuration; ambient HTTP_PROXY /
        # ALL_PROXY environment variables must not silently reroute requests.
        trust_env=False,
        follow_redirects=True,
        max_redirects=max(0, net_cfg.max_redirects),
        headers=dict(_DEFAULT_HEADERS),
    )


def _spawn_aclose(client: httpx.AsyncClient, loop: asyncio.AbstractEventLoop) -> None:
    """Close *client* on its owning loop (runs inside that loop)."""
    coro = client.aclose()
    try:
        loop.create_task(coro)
    except RuntimeError:
        coro.close()  # loop closed concurrently; GC reaps the client


def _close_evicted_client(client: httpx.AsyncClient, loop: asyncio.AbstractEventLoop | None) -> None:
    """Best-effort close of an evicted pooled client on its owning loop."""
    if client.is_closed or loop is None or loop.is_closed():
        return  # already closed, or no live loop: let GC handle it
    try:
        loop.call_soon_threadsafe(_spawn_aclose, client, loop)
    except RuntimeError:
        # Loop closed between the liveness check and the call - GC handles it.
        logger.debug("event loop closed before an evicted client could be closed")


def get_shared_client(proxy: str | None = None, timeout: float | None = None) -> httpx.AsyncClient:
    """Return a pooled ``AsyncClient`` for the current loop/proxy/timeout.

    Args:
        proxy: Proxy URL (``None`` for direct connections).
        timeout: Request timeout override (defaults to
            ``HttpPoolConfig.provider_timeout``). Part of the pool key, so a
            pooled hit can never silently ignore a different timeout.

    Returns:
        A reusable ``httpx.AsyncClient``.
    """
    cfg = get_config().http
    resolved_timeout = float(timeout if timeout is not None else cfg.provider_timeout)
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
    except RuntimeError:
        loop = None
        loop_id = 0  # no loop yet - client will be created lazily on first use

    key = (loop_id, proxy or None, resolved_timeout)
    entry = _pool.get(key)
    if entry is not None and not entry[0].is_closed:
        return entry[0]

    with _pool_lock:
        entry = _pool.get(key)
        if entry is not None and not entry[0].is_closed:
            return entry[0]
        # Evict closed/stale entries so the pool does not grow unbounded.
        stale = [
            k for k, (client, _client_loop) in _pool.items() if client.is_closed or (k[0] != 0 and k[0] != loop_id)
        ]
        for k in stale:
            evicted = _pool.pop(k, None)
            if evicted is not None:
                _close_evicted_client(evicted[0], evicted[1])
        client = _build_client(proxy or None, resolved_timeout)
        _pool[key] = (client, loop)
        return client


def resolve_provider_proxy(url: str) -> str | None:
    """Proxy to use for a provider URL, honoring the no_proxy bypass list."""
    return get_config().network.resolve_proxy_for_url(url)


async def aclose_all() -> None:
    """Close all pooled clients (for clean shutdown / tests)."""
    with _pool_lock:
        entries = list(_pool.values())
        _pool.clear()
    for client, _loop in entries:
        try:
            await client.aclose()
        except Exception as exc:
            logger.warning("failed to close pooled HTTP client: {}", exc)
