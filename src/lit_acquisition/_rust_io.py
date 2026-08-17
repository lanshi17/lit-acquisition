"""Optional Rust native extension loader.

When the ``rust-io`` package is installed, HTTP I/O is handled by the
fast Rust extension.  When it is not available, the module falls back
to ``None``, and the gateway uses ``httpx`` as a pure-Python fallback.

Install the Rust extension for better performance::

    pip install rust-io
"""

from __future__ import annotations

from loguru import logger

NET_AVAILABLE: bool = False
net_io = None

_NATIVE_IMPORT_ERRORS = (ImportError, RuntimeError, SystemError, OSError)

try:
    import rust_io.net as net_io

    NET_AVAILABLE = True
except _NATIVE_IMPORT_ERRORS:
    try:
        import net_io  # noqa: F401

        NET_AVAILABLE = True
    except _NATIVE_IMPORT_ERRORS:
        logger.debug("net_io / rust_io.net not available - using httpx fallback for HTTP I/O")
