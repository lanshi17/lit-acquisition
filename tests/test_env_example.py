"""Guards for the `.env.example` template.

The template is the only documented place listing every knob, so it must stay
in sync with what the code actually reads. These tests fail when a new
environment variable is introduced without being documented, or when a real
secret is accidentally committed into the template.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
SRC_DIR = REPO_ROOT / "src" / "lit_acquisition"
SERVICE_DIR = REPO_ROOT / "server" / "src"

DECLARED = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)
ACTIVE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=(.*)$", re.MULTILINE)
GETENV = re.compile(r'os\.getenv\(\s*"([A-Z][A-Z0-9_]*)"')
RUST_SERVICE_KEY = re.compile(r'"(LIT_SERVICE_[A-Z_]+)"')
SECRET_KEYS = ("API_KEY", "API_KEYS", "EMAIL", "TOKEN", "SECRET", "PASSWORD")


def _declared_keys() -> set[str]:
    return set(DECLARED.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


def _python_env_keys() -> set[str]:
    keys: set[str] = set()
    for path in SRC_DIR.rglob("*.py"):
        keys.update(GETENV.findall(path.read_text(encoding="utf-8")))
    return keys


def _rust_service_keys() -> set[str]:
    keys: set[str] = set()
    for path in SERVICE_DIR.glob("*.rs"):
        keys.update(RUST_SERVICE_KEY.findall(path.read_text(encoding="utf-8")))
    return keys


def test_template_exists() -> None:
    assert ENV_EXAMPLE.is_file(), ".env.example must exist at the repository root"
    assert ENV_EXAMPLE.stat().st_size > 0


def test_every_python_env_var_is_documented() -> None:
    missing = sorted(_python_env_keys() - _declared_keys())
    assert not missing, f"environment variables missing from .env.example: {missing}"


def test_every_service_env_var_is_documented() -> None:
    missing = sorted(_rust_service_keys() - _declared_keys())
    assert not missing, f"service variables missing from .env.example: {missing}"


def test_service_port_and_auth_are_documented() -> None:
    keys = _declared_keys()
    for required in (
        "LIT_SERVICE_ADDR",
        "LIT_SERVICE_HOST",
        "LIT_SERVICE_PORT",
        "LIT_SERVICE_API_KEY",
        "LIT_SERVICE_PYTHON",
        "LIT_SERVICE_PYTHONPATH",
        "LIT_SERVICE_WORKERS",
        "LIT_SERVICE_REQUEST_TIMEOUT",
        "LIT_SERVICE_MAX_BODY_BYTES",
    ):
        assert required in keys, f"{required} must be listed in .env.example"


def test_no_secret_value_is_filled_in() -> None:
    for key, value in ACTIVE.findall(ENV_EXAMPLE.read_text(encoding="utf-8")):
        value = value.strip().strip('"').strip("'")
        if any(marker in key for marker in SECRET_KEYS):
            assert value == "", f"{key} must be left empty in .env.example, got {value!r}"
        assert not value.startswith(("sk-", "sk_", "ghp_", "fc-", "tvly-")), (
            f"{key} looks like a real credential: {value!r}"
        )


def test_template_is_not_gitignored() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "!.env.example" in gitignore, ".env.example must stay trackable"
    assert ".env" in gitignore, "the real .env must stay ignored"

def test_template_has_no_unknown_variables() -> None:
    """Every key in the template must be read by the code (guards typos)."""
    known = _python_env_keys() | _rust_service_keys() | {"RUST_LOG"}
    unknown = sorted(_declared_keys() - known)
    assert not unknown, f".env.example documents variables nothing reads: {unknown}"
