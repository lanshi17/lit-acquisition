"""Network security helpers: SSRF protection and secret redaction.

SSRF
----
``validate_url_safe`` rejects URLs that target private/reserved address
space. It is applied to every download candidate *and* to every hop of an
HTTP redirect chain (see :mod:`net.download`), so a malicious server cannot
bounce a request into the internal network via a redirect.

Residual risk (documented): validation resolves DNS at check time while the
HTTP client resolves again at connect time, so a determined attacker with a
rebinding DNS server could still race the two lookups. Fully closing that
requires pinning the resolved IP into the transport, which is out of scope
here; per-hop validation nonetheless blocks the common static-redirect and
direct-internal cases.

Privacy
-------
``redact_secrets`` masks the values of sensitive query parameters (api keys,
tokens, emails) so they never reach logs, warnings, or the agent-visible
response. PubMed's ``api_key`` and Unpaywall's ``email`` are both passed as
URL query parameters, so any exception that embeds the request URL would
otherwise leak them.
"""

from __future__ import annotations

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
# proxy. Both the username and password are masked.
_USERINFO_RE = re.compile(r"(?i)(^[a-z][a-z0-9+.\-]*://)([^:/@\s]+):([^@\s]+)@")


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
