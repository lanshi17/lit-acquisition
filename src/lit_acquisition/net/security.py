"""Network security helpers: SSRF protection and secret redaction.

SSRF
----
``validate_url_safe`` rejects URLs that target private/reserved address
space. It is applied to every download candidate *and* to every hop of an
HTTP redirect chain (see :mod:`net.download`), so a malicious server cannot
bounce a request into the internal network via a redirect.

Residual risk (documented): ``validate_url_safe`` resolves DNS at check
time. The direct download path (:func:`net.download.secure_download`)
closes the rebinding window by resolving via :func:`resolve_safe_ip` and
pinning that validated IP into the transport, so the connection is made
to the exact address that passed validation. The proxied metadata path
connects to the proxy (which does its own resolution), so pinning is moot
there.

Privacy
-------
``redact_secrets`` masks the values of sensitive query parameters (api keys,
tokens, emails) so they never reach logs, warnings, or the agent-visible
response. PubMed's ``api_key`` and Unpaywall's ``email`` are both passed as
URL query parameters, so any exception that embeds the request URL would
otherwise leak them.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlparse


def _unwrap_ip(raw: str) -> ipaddress._BaseAddress:
    """Parse an address, unwrapping IPv4-mapped IPv6 (``::ffff:a.b.c.d``) so
    the embedded IPv4 address is the one that gets judged."""
    ip = ipaddress.ip_address(raw)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _is_unsafe_ip(ip: ipaddress._BaseAddress) -> bool:
    """True for any address that is not globally routable.

    ``is_global`` is False for private, loopback, link-local, reserved,
    multicast, the unspecified address, CGN (100.64/10), benchmarking and
    other special-purpose ranges - a superset of the old explicit checks.
    """
    if ip.is_unspecified:
        return True
    return not ip.is_global


def is_private_ip(hostname: str) -> bool:
    """Return True if *hostname* resolves to a private/reserved IP address."""
    try:
        addrinfos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return True  # treat unresolvable hosts as unsafe
    if not addrinfos:
        return True
    for _family, _type, _proto, _canonname, sockaddr in addrinfos:
        try:
            ip = _unwrap_ip(sockaddr[0])
        except ValueError:
            return True
        if _is_unsafe_ip(ip):
            return True
    return False

async def resolve_safe_ip(hostname: str) -> str:
    """Resolve *hostname* and return the first globally-routable IP address.

    Runs the (blocking) DNS lookup in a worker thread so the event loop is
    never stalled. The returned IP can be pinned into the HTTP transport so
    the connection is made to the exact address that was validated — this
    closes the DNS-rebinding TOCTOU gap between validation and connect time.

    Raises:
        ValueError: If the host does not resolve or only resolves to
            private/reserved addresses.
    """

    def _resolve() -> str:
        try:
            addrinfos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise ValueError(f"could not resolve hostname: {hostname}") from exc
        for _family, _type, _proto, _canonname, sockaddr in addrinfos:
            try:
                ip = _unwrap_ip(sockaddr[0])
            except ValueError:
                continue
            if not _is_unsafe_ip(ip):
                return str(ip)
        raise ValueError(f"hostname resolves only to private/reserved addresses: {hostname}")
    return await asyncio.to_thread(_resolve)

def validate_url_safe(url: str) -> None:
    """Raise ValueError if *url* targets a private/reserved IP (SSRF guard).

    Raises:
        ValueError: When the URL scheme is not http/https or the target
            hostname resolves to a private/reserved address.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError(f"URL has no hostname: {redact_secrets(url)}")
    if is_private_ip(hostname):
        raise ValueError(f"URL targets a private/reserved address: {redact_secrets(url)}")


# Query parameters whose values are secrets / PII and must never be logged or
# surfaced. Matching is case-insensitive; values are masked, names kept.
_SECRET_PARAM_RE = re.compile(r"(?i)([?&](?:api_?key|apikey|access_?token|token|secret|auth|email)=)([^&#\s]+)")

# URL userinfo credentials (``scheme://user:pass@host``), e.g. an authenticated
# proxy. Both the username and password are masked. Unanchored on purpose so
# credentials embedded mid-string (e.g. inside a logged message quoting the
# URL) are redacted too, not only URLs that start with the scheme.
_USERINFO_RE = re.compile(r"(?i)([a-z][a-z0-9+.\-]*://)([^:/@\s]+):([^@\s]+)@")


def redact_secrets(text: str) -> str:
    """Mask sensitive values (query-param secrets and userinfo credentials) in
    URLs or error text.

    Safe to call on arbitrary strings; returns the input with secret values
    replaced by ``REDACTED``.
    """
    if not text:
        return text
    out = str(text)
    out = _SECRET_PARAM_RE.sub(lambda m: m.group(1) + "REDACTED", out)
    out = _USERINFO_RE.sub(lambda m: m.group(1) + "REDACTED:REDACTED@", out)
    return out
