"""Secure file download.

Handles the network download path: SSRF-safe fetching with per-hop redirect
validation, a size cap, and HTML->PDF link extraction. This is the only place
that performs bulk content retrieval; provider metadata fetches live in
:mod:`providers` and :mod:`gateway`.
"""

from __future__ import annotations

import asyncio
import errno
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from loguru import logger

from .._rust_io import net_io
from ..config import get_config
from ..utils.text import sanitize_filename
from .pool import build_pinned_client, get_shared_client
from .security import redact_secrets, resolve_safe_ip, validate_url_safe

_PDF_LINK_PATTERNS = [
    re.compile(r'href=["\']([^"\']*\.pdf[^"\']*)["\']', re.IGNORECASE),
    re.compile(r'href=["\']([^"\']*download[^"\']*pdf[^"\']*)["\']', re.IGNORECASE),
]


class DownloadSecurityError(RuntimeError):
    """A download was blocked by a security policy (SSRF, redirect-loop, or
    oversized body)."""


_DISK_FULL_ERRNOS = {errno.ENOSPC, errno.EIO, errno.EROFS, errno.EDQUOT}

# Maximum number of URLs attempted per download call (initial URL + HTML pages
# chased for embedded PDF links), so a pathological page cannot fan out into
# hundreds of full downloads.
MAX_LINK_CHASE = 10


def _atomic_write(target: Path, data: bytes) -> None:
    """Write *data* to *target* via a temp file + atomic rename.

    A crash or ``ENOSPC`` mid-write leaves no truncated ``.pdf`` behind: the
    partial ``.part`` file is unlinked and the previous target (if any) stays
    intact.
    """
    tmp = target.with_name(target.name + ".part")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise

def extract_pdf_links_from_html(html: str, base_url: str) -> list[str]:
    """Extract absolute PDF link candidates from an HTML page."""
    links: list[str] = []
    for pattern in _PDF_LINK_PATTERNS:
        for match in pattern.finditer(html):
            href = match.group(1)
            absolute = urljoin(base_url, href)
            if absolute not in links:
                links.append(absolute)
    return links


async def secure_fetch(
    client: Any,
    url: str,
    *,
    timeout: float,
    max_redirects: int,
    max_bytes: int,
) -> tuple[bytes, str, str]:
    """GET *url* following redirects with per-hop SSRF validation and a size
    cap. Returns ``(content, final_url, content_type)``.

    Security properties:

    * each redirect hop is validated against ``validate_url_safe`` *before* a
      connection is made, so a server cannot bounce the request into
      private/internal address space via a redirect;
    * the body is streamed and truncated at ``max_bytes`` (and rejected early
      when ``Content-Length`` already exceeds it), bounding memory use against
      a hostile or broken server.
    """
    current = url
    for _hop in range(max_redirects + 1):
        # Blocking DNS resolution must not run on the event loop (a slow
        # resolver would freeze every concurrent provider/download).
        await asyncio.to_thread(validate_url_safe, current)
        async with client.stream("GET", current, follow_redirects=False, timeout=timeout) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    # A redirect without Location is not a usable response;
                    # raise_for_status() is a no-op for 3xx, so surface it.
                    raise DownloadSecurityError(
                        f"redirect_without_location: HTTP {resp.status_code} from {redact_secrets(current)}"
                    )
                current = urljoin(str(current), location)
                continue
            resp.raise_for_status()
            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise DownloadSecurityError(
                    f"download_too_large: content-length {content_length} exceeds {max_bytes} bytes"
                )
            content_type = str(resp.headers.get("content-type") or "").lower()
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise DownloadSecurityError(f"download_too_large: exceeded {max_bytes} bytes")
                chunks.append(chunk)
            return b"".join(chunks), current, content_type
    raise DownloadSecurityError(f"too_many_redirects: exceeded {max_redirects}")


async def secure_download(
    client: Any,
    pins: dict[str, str],
    url: str,
    *,
    timeout: float,
    max_redirects: int,
    max_bytes: int,
    tmp_path: Path,
) -> tuple[str, str]:
    """GET *url* following redirects, streaming the body straight to *tmp_path*.

    Same security properties as :func:`secure_fetch` — per-hop SSRF validation
    and a size cap — plus two production properties:

    * the body is never buffered whole in memory (memory use is O(chunk size),
      not O(file size));
    * each hop's hostname is resolved to a validated IP which is pinned for
      the connection (recorded in *pins*), closing the DNS-rebinding window
      between validation and connect (honored by a pinned direct client;
      proxied clients connect to the proxy regardless).

    The caller is responsible for renaming *tmp_path* to the final location
    (or deleting it) based on the returned content type.

    Returns ``(final_url, content_type)``.
    """
    current = url
    for _hop in range(max_redirects + 1):
        parsed = urlparse(current)
        hostname = parsed.hostname or ""
        if not hostname:
            raise DownloadSecurityError(f"redirect target has no hostname: {redact_secrets(current)}")
        ip = await resolve_safe_ip(hostname)  # raises ValueError on private/unresolvable
        pins[hostname] = ip
        async with client.stream("GET", current, follow_redirects=False, timeout=timeout) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    raise DownloadSecurityError(
                        f"redirect_without_location: HTTP {resp.status_code} from {redact_secrets(current)}"
                    )
                current = urljoin(str(current), location)
                continue
            resp.raise_for_status()
            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise DownloadSecurityError(
                    f"download_too_large: content-length {content_length} exceeds {max_bytes} bytes"
                )
            content_type = str(resp.headers.get("content-type") or "").lower()
            total = 0
            with tmp_path.open("wb") as fh:
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise DownloadSecurityError(f"download_too_large: exceeded {max_bytes} bytes")
                    fh.write(chunk)
            return current, content_type
    raise DownloadSecurityError(f"too_many_redirects: exceeded {max_redirects}")


async def download_file_from_url(
    url: str,
    download_path: str,
    filename_stem: str,
) -> tuple[str | None, str | None, list[str]]:
    """Download a file from a direct URL. Handles HTML->PDF redirect.

    If the URL returns PDF bytes (magic ``%PDF``), saves directly. If the URL
    returns HTML, extracts PDF links from the page and retries each candidate.

    Proxy routing: international sites go through the configured proxy;
    mainland China sites connect directly (see ``NetworkConfig``).

    Args:
        url: Direct download URL.
        download_path: Directory to save the file.
        filename_stem: Base filename (without extension).

    Returns:
        (file_path, final_url, warnings) tuple.
    """
    warnings: list[str] = []
    target = Path(download_path) / f"{sanitize_filename(filename_stem)}.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    proxy_resolver = get_config().network.resolve_proxy_for_url

    queue: list[str] = [url]
    visited: set[str] = set()

    net_cfg = get_config().network
    max_bytes = net_cfg.max_download_bytes

    while queue:
        if len(visited) >= MAX_LINK_CHASE:
            warnings.append(f"link_chase_limit:exceeded {MAX_LINK_CHASE} attempts:{redact_secrets(url)}")
            break
        current_url = queue.pop(0)
        if current_url in visited:
            continue
        visited.add(current_url)

        # Blocking DNS resolution must not run on the event loop.
        try:
            await asyncio.to_thread(validate_url_safe, current_url)
        except ValueError as ssrf_exc:
            warnings.append(f"ssrf_blocked:{redact_secrets(current_url)}:{redact_secrets(str(ssrf_exc))}")
            continue

        proxy = proxy_resolver(current_url)

        # Try Rust download first (faster, has built-in retry)
        if net_io is not None:
            try:
                timeout_ms = int(get_config().http.download_timeout * 1000)
                result = await net_io.download_file(current_url, timeout_ms=timeout_ms, proxy=proxy)
                status = result.get("status_code", 0)
                file_bytes: bytes = result.get("bytes", b"")
                final_url: str = result.get("final_url", current_url)

                if status == 0 or status >= 400:
                    warnings.append(f"download_http_{status}:{redact_secrets(current_url)}")
                    continue

                # The Rust client follows redirects internally; its hops are
                # not visible, so re-validate the *final* URL. If it redirected
                # anywhere unsafe, discard the body and use the httpx path
                # (which validates every hop before connecting).
                unsafe_redirect = False
                if final_url != current_url:
                    try:
                        await asyncio.to_thread(validate_url_safe, final_url)
                    except ValueError:
                        warnings.append(
                            f"ssrf_redirect_fallback:{redact_secrets(current_url)}->{redact_secrets(final_url)}"
                        )
                        unsafe_redirect = True

                if unsafe_redirect:
                    # The Rust client followed a redirect to an unsafe host;
                    # discard the body and let the httpx path (which validates
                    # every hop *before* connecting) handle the URL.
                    pass
                elif file_bytes:
                    if len(file_bytes) > max_bytes:
                        warnings.append(
                            f"download_too_large:{len(file_bytes)} bytes exceeds {max_bytes}:{redact_secrets(current_url)}"
                        )
                        continue

                    if file_bytes[:4] == b"%PDF":
                        _atomic_write(target, file_bytes)
                        return str(target), final_url, warnings

                    if b"<html" in file_bytes[:2048].lower():
                        extra_links = extract_pdf_links_from_html(
                            file_bytes.decode("utf-8", errors="replace"), final_url or current_url
                        )
                        for link in extra_links:
                            if link not in visited and len(queue) < MAX_LINK_CHASE:
                                queue.append(link)
                        continue

                    warnings.append(f"non_pdf_content:{redact_secrets(current_url)}")
                    continue


            except OSError as exc:
                # Disk-full / I-O write failures: re-downloading cannot help.
                if exc.errno in _DISK_FULL_ERRNOS:
                    warnings.append(f"disk_write_error:{exc.errno}:{redact_secrets(str(exc))}")
                    logger.error("download write failed (disk/I-O error {}): {}", exc.errno, exc)
                    return None, None, warnings
                warnings.append(f"rust_download_error:{redact_secrets(current_url)}:{redact_secrets(str(exc))}")
                # Fall through to httpx fallback
            except Exception as exc:
                logger.debug("rust download_file failed for {}: {}", current_url, exc)
                warnings.append(f"rust_download_error:{redact_secrets(current_url)}:{redact_secrets(str(exc))}")
                # Fall through to httpx fallback

        # httpx path. For direct connections use a pinned client: every hop
        # is resolved to a validated IP and connected to exactly that IP
        # (no DNS-rebinding window), and the body streams straight to a
        # spool file instead of being buffered whole in memory.
        spool = target.with_name(target.stem + ".spool")
        spool_created = False
        pinned_client = None
        try:
            pins: dict[str, str] = {}
            if proxy:
                # Proxy connections dial the proxy, not the origin - IP
                # pinning is moot; keep the shared (possibly proxied) client.
                client = get_shared_client(proxy=proxy)
                content, final_url, content_type = await secure_fetch(
                    client,
                    current_url,
                    timeout=get_config().http.download_timeout,
                    max_redirects=net_cfg.max_redirects,
                    max_bytes=max_bytes,
                )
                if content.startswith(b"%PDF"):
                    _atomic_write(target, content)
                    return str(target), final_url, warnings
                if "html" in content_type or b"<html" in content[:2048].lower():
                    for link in extract_pdf_links_from_html(
                        content.decode("utf-8", errors="replace"), current_url
                    ):
                        if link not in visited and len(queue) < MAX_LINK_CHASE:
                            queue.append(link)
                    continue
                warnings.append(f"non_pdf_content_type:{content_type or 'unknown'}:{redact_secrets(current_url)}")
                continue

            pinned_client = build_pinned_client(pins, timeout=get_config().http.download_timeout)
            spool_created = True
            final_url, content_type = await secure_download(
                pinned_client,
                pins,
                current_url,
                timeout=get_config().http.download_timeout,
                max_redirects=net_cfg.max_redirects,
                max_bytes=max_bytes,
                tmp_path=spool,
            )
            with spool.open("rb") as fh:
                head = fh.read(2048)
            if head[:4] == b"%PDF":
                os.replace(spool, target)  # spool is complete -> publish atomically
                spool_created = False
                return str(target), final_url, warnings
            if "html" in content_type or b"<html" in head.lower():
                html_text = spool.read_text(encoding="utf-8", errors="replace")
                for link in extract_pdf_links_from_html(html_text, final_url or current_url):
                    if link not in visited and len(queue) < MAX_LINK_CHASE:
                        queue.append(link)
                continue
            warnings.append(f"non_pdf_content_type:{content_type or 'unknown'}:{redact_secrets(current_url)}")

        except DownloadSecurityError as sec_exc:
            warnings.append(f"download_blocked:{redact_secrets(current_url)}:{redact_secrets(str(sec_exc))}")
        except ValueError as ssrf_exc:
            warnings.append(f"ssrf_blocked:{redact_secrets(current_url)}:{redact_secrets(str(ssrf_exc))}")
        except OSError as exc:
            if exc.errno in _DISK_FULL_ERRNOS:
                warnings.append(f"disk_write_error:{exc.errno}:{redact_secrets(str(exc))}")
                logger.error("download write failed (disk/I-O error {}): {}", exc.errno, exc)
                return None, None, warnings
            warnings.append(f"download_error:{redact_secrets(current_url)}:{redact_secrets(str(exc))}")
        except Exception as exc:
            warnings.append(f"download_error:{redact_secrets(current_url)}:{redact_secrets(str(exc))}")
        finally:
            if spool_created:
                try:
                    spool.unlink(missing_ok=True)
                except OSError:
                    pass
            if pinned_client is not None:
                await pinned_client.aclose()

    return None, None, warnings
