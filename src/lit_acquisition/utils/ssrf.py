"""SSRF protection - URL safety validation."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def is_private_ip(hostname: str) -> bool:
    """Return True if *hostname* resolves to a private/reserved IP address."""
    try:
        addrinfos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return True  # treat unresolvable hosts as unsafe

    for _family, _type, _proto, _canonname, sockaddr in addrinfos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
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
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    hostname = parsed.hostname or ""
    if not hostname or is_private_ip(hostname):
        raise ValueError(f"URL targets a private/reserved address: {url}")
