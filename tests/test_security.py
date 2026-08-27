"""Security tests: SSRF protection, secret redaction, and secure downloads."""

from __future__ import annotations

import pytest

from lit_acquisition.config import LitAcquisitionConfig
from lit_acquisition.net.download import DownloadSecurityError, secure_download, secure_fetch
from lit_acquisition.net.security import is_private_ip, redact_secrets, validate_url_safe

# ── is_private_ip ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # RFC1918
        "192.168.1.1",  # RFC1918
        "172.16.0.1",  # RFC1918
        "169.254.1.1",  # link-local
        "0.0.0.0",  # unspecified
        "100.64.0.1",  # CGN (shared address space)
        "::1",  # IPv6 loopback
    ],
)
def test_is_private_ip_blocks(host):
    assert is_private_ip(host) is True


def test_is_private_ip_blocks_ipv4_mapped_ipv6():
    # ::ffff:127.0.0.1 must be judged by its embedded IPv4 address.
    assert is_private_ip("::ffff:127.0.0.1") is True


def test_is_private_ip_allows_global():
    assert is_private_ip("8.8.8.8") is False


def test_is_private_ip_unresolvable_is_unsafe():
    # A hostname that fails to resolve is treated as unsafe (fail closed).
    assert is_private_ip("definitely-not-a-real-host.invalid") is True


# ── validate_url_safe ──────────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x/", "javascript:alert(1)"])
def test_validate_url_rejects_bad_scheme(url):
    with pytest.raises(ValueError):
        validate_url_safe(url)


def test_validate_url_rejects_internal():
    with pytest.raises(ValueError):
        validate_url_safe("http://127.0.0.1/secret")
    with pytest.raises(ValueError):
        validate_url_safe("http://[::1]/secret")


def test_validate_url_rejects_no_hostname():
    with pytest.raises(ValueError):
        validate_url_safe("http:///nohost")


def test_validate_url_allows_global_http_and_https():
    validate_url_safe("http://8.8.8.8/x")
    validate_url_safe("https://8.8.8.8/x")


# ── redact_secrets ─────────────────────────────────────────────────────────


def test_redact_api_key():
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&api_key=SECRET123&retmax=5"
    out = redact_secrets(url)
    assert "SECRET123" not in out
    assert "api_key=REDACTED" in out
    assert "db=pubmed" in out  # non-secret params preserved


def test_redact_email():
    url = "https://api.unpaywall.org/v2/10.1/x?email=user@example.com"
    out = redact_secrets(url)
    assert "user@example.com" not in out
    assert "email=REDACTED" in out


def test_redact_token_and_case_insensitive():
    out = redact_secrets("https://x.org/?Token=abc&ACCESS_TOKEN=def")
    assert "abc" not in out and "def" not in out


def test_redact_leaves_benign_text():
    assert redact_secrets("https://x.org/?rows=5&q=cancer") == "https://x.org/?rows=5&q=cancer"
    assert redact_secrets("") == ""


def test_redact_userinfo_credentials():
    out = redact_secrets("http://proxyuser:proxypass@10.0.0.1:8080/path")
    assert "proxyuser" not in out and "proxypass" not in out
    assert "REDACTED:REDACTED@" in out
    assert "10.0.0.1:8080/path" in out  # host/path preserved


def test_redact_userinfo_in_socks_proxy():
    out = redact_secrets("socks5://someuser:somepass@127.0.0.1:1080")
    assert "someuser" not in out and "somepass" not in out
    assert "REDACTED:REDACTED@" in out
    assert "127.0.0.1:1080" in out


def test_redact_no_userinfo_untouched():
    url = "https://api.example.com/v1/works"
    assert redact_secrets(url) == url


# ── config bounds ──────────────────────────────────────────────────────────


def test_network_config_defaults_and_clamps():
    cfg = LitAcquisitionConfig()
    assert cfg.network.max_redirects == 5
    assert cfg.network.max_download_bytes == 200 * 1024 * 1024


# ── secure_fetch (fake httpx client, no network) ─────────────────────────


class _FakeResp:
    def __init__(self, status_code=200, headers=None, chunks=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def aiter_bytes(self):
        async def gen():
            for c in self._chunks:
                yield c

        return gen()


class _FakeStreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, responder):
        self._responder = responder  # fn(url) -> _FakeResp

    def stream(self, method, url, **kwargs):
        return _FakeStreamCtx(self._responder(url))


@pytest.mark.asyncio
async def test_secure_fetch_blocks_redirect_to_internal():
    def responder(url):
        # First hop is public but redirects to loopback.
        return _FakeResp(status_code=302, headers={"location": "http://127.0.0.1/internal"})

    client = _FakeClient(responder)
    with pytest.raises(ValueError):
        await secure_fetch(client, "http://8.8.8.8/start", timeout=5, max_redirects=5, max_bytes=1024)


@pytest.mark.asyncio
async def test_secure_fetch_size_cap():
    def responder(url):
        return _FakeResp(status_code=200, headers={}, chunks=[b"A" * 64, b"B" * 64])

    client = _FakeClient(responder)
    with pytest.raises(DownloadSecurityError):
        await secure_fetch(client, "http://8.8.8.8/big", timeout=5, max_redirects=5, max_bytes=100)


@pytest.mark.asyncio
async def test_secure_fetch_content_length_precheck():
    def responder(url):
        return _FakeResp(status_code=200, headers={"content-length": "999999"}, chunks=[b"x"])

    client = _FakeClient(responder)
    with pytest.raises(DownloadSecurityError):
        await secure_fetch(client, "http://8.8.8.8/big", timeout=5, max_redirects=5, max_bytes=100)


@pytest.mark.asyncio
async def test_secure_fetch_success_and_redirect_limit():
    def responder(url):
        return _FakeResp(status_code=200, headers={"content-type": "application/pdf"}, chunks=[b"%PDF-1.4"])

    client = _FakeClient(responder)
    content, final_url, ctype = await secure_fetch(
        client, "http://8.8.8.8/doc.pdf", timeout=5, max_redirects=5, max_bytes=1024
    )
    assert content == b"%PDF-1.4"
    assert final_url == "http://8.8.8.8/doc.pdf"
    assert ctype == "application/pdf"

    # Endless redirect loop must terminate with DownloadSecurityError.
    def redirect_loop(url):
        return _FakeResp(status_code=302, headers={"location": "http://8.8.8.8/loop"})

    loop_client = _FakeClient(redirect_loop)
    with pytest.raises(DownloadSecurityError):
        await secure_fetch(loop_client, "http://8.8.8.8/a", timeout=5, max_redirects=3, max_bytes=1024)


# ── download_file_from_url (Rust-path cap + atomic writes) ────────────────


@pytest.mark.asyncio
async def test_download_rust_path_enforces_size_cap(tmp_path, monkeypatch):
    """The Rust download path must honor max_download_bytes (OOM guard)."""
    from lit_acquisition.net import download as dl

    class _FakeNetIo:
        async def download_file(self, url, timeout_ms=0, proxy=None):
            return {
                "status_code": 200,
                "final_url": url,
                "bytes": b"%PDF-1.4" + b"x" * 4096,
            }

    monkeypatch.setattr(dl, "net_io", _FakeNetIo())
    monkeypatch.setattr(dl.get_config().network, "max_download_bytes", 1024)

    file_path, _final_url, warnings = await dl.download_file_from_url(
        "https://example.com/paper.pdf", str(tmp_path), "paper"
    )
    assert file_path is None
    assert any("too_large" in w for w in warnings)
    assert not list(tmp_path.glob("*.pdf"))


@pytest.mark.asyncio
async def test_download_rust_path_atomic_write_no_partial(tmp_path, monkeypatch):
    """A successful download leaves only the final file, never a .part."""
    from lit_acquisition.net import download as dl

    class _FakeNetIo:
        async def download_file(self, url, timeout_ms=0, proxy=None):
            return {"status_code": 200, "final_url": url, "bytes": b"%PDF-1.4 content"}

    monkeypatch.setattr(dl, "net_io", _FakeNetIo())

    file_path, _final_url, _warnings = await dl.download_file_from_url(
        "https://example.com/paper.pdf", str(tmp_path), "paper"
    )
    assert file_path is not None
    assert (tmp_path / "paper.pdf").read_bytes().startswith(b"%PDF")
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.asyncio
async def test_download_rust_oversize_does_not_write_partial(tmp_path, monkeypatch):
    """Oversized Rust responses are rejected before any write."""
    from lit_acquisition.net import download as dl

    class _FakeNetIo:
        async def download_file(self, url, timeout_ms=0, proxy=None):
            return {"status_code": 200, "final_url": url, "bytes": b"%PDF" + b"z" * 99999}

    monkeypatch.setattr(dl, "net_io", _FakeNetIo())
    monkeypatch.setattr(dl.get_config().network, "max_download_bytes", 1024)

    file_path, _, _ = await dl.download_file_from_url(
        "https://example.com/big.pdf", str(tmp_path), "big"
    )
    assert file_path is None
    assert list(tmp_path.iterdir()) == []


# ── secure_download (pinned streaming to spool file) ──────────────────────


@pytest.mark.asyncio
async def test_secure_download_streams_to_file_and_pins(tmp_path, monkeypatch):
    from lit_acquisition.net import download as dl

    async def fake_resolve(hostname):
        return "93.184.216.34"  # pretend host resolves to a public IP

    monkeypatch.setattr(dl, "resolve_safe_ip", fake_resolve)

    def responder(url):
        return _FakeResp(
            status_code=200,
            headers={"content-type": "application/pdf"},
            chunks=[b"%PDF-1.4", b" body"],
        )

    client = _FakeClient(responder)
    spool = tmp_path / "x.spool"
    pins: dict = {}
    final_url, ctype = await secure_download(
        client,
        pins,
        "http://example.com/doc.pdf",
        timeout=5,
        max_redirects=5,
        max_bytes=1024,
        tmp_path=spool,
    )
    assert final_url == "http://example.com/doc.pdf"
    assert ctype == "application/pdf"
    assert spool.read_bytes() == b"%PDF-1.4 body"
    assert pins == {"example.com": "93.184.216.34"}


@pytest.mark.asyncio
async def test_secure_download_cap_rejects_oversized(tmp_path, monkeypatch):
    from lit_acquisition.net import download as dl

    async def fake_resolve(hostname):
        return "93.184.216.34"

    monkeypatch.setattr(dl, "resolve_safe_ip", fake_resolve)

    def responder(url):
        return _FakeResp(status_code=200, headers={}, chunks=[b"b" * 4096])

    client = _FakeClient(responder)
    spool = tmp_path / "x.spool"
    with pytest.raises(DownloadSecurityError):
        await secure_download(
            client,
            {},
            "http://example.com/big.pdf",
            timeout=5,
            max_redirects=5,
            max_bytes=100,
            tmp_path=spool,
        )
    # Partial spool left for caller to clean up; never silently treated as complete.


@pytest.mark.asyncio
async def test_secure_download_blocks_private_target(tmp_path, monkeypatch):
    from lit_acquisition.net import download as dl

    async def fake_resolve(hostname):
        raise ValueError(f"hostname resolves only to private/reserved addresses: {hostname}")

    monkeypatch.setattr(dl, "resolve_safe_ip", fake_resolve)

    client = _FakeClient(lambda url: _FakeResp(200, {}, [b"x"]))
    spool = tmp_path / "x.spool"
    with pytest.raises(ValueError):
        await secure_download(
            client,
            {},
            "http://intranet.internal/doc.pdf",
            timeout=5,
            max_redirects=5,
            max_bytes=1024,
            tmp_path=spool,
        )
