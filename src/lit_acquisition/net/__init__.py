"""Network layer: connection pool, SSRF/secret security, secure downloads."""

from __future__ import annotations

from .download import (
    DownloadSecurityError,
    download_file_from_url,
    extract_pdf_links_from_html,
    secure_download,
    secure_fetch,
)
from .pool import aclose_all, build_pinned_client, get_shared_client, resolve_provider_proxy
from .security import is_private_ip, redact_secrets, resolve_safe_ip, validate_url_safe

__all__ = [
    "DownloadSecurityError",
    "aclose_all",
    "build_pinned_client",
    "download_file_from_url",
    "extract_pdf_links_from_html",
    "get_shared_client",
    "is_private_ip",
    "redact_secrets",
    "resolve_provider_proxy",
    "resolve_safe_ip",
    "secure_download",
    "secure_fetch",
    "validate_url_safe",
]
