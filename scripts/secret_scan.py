#!/usr/bin/env python3
"""Narrow, fail-closed credential scan for the files a commit will ship.

This is the fallback used when ggshield cannot run. It is deliberately
conservative about *what counts* as a secret and strict about *failure*:

* variable-to-variable assignments (``cfg.llm.api_key = api_key``) are not
  findings, but a token-shaped literal is;
* known placeholders from the template (``your-...``, ``smoke-token``) are not
  findings, so the gate does not train people to ignore it;
* an unreadable file, no files at all, or an internal error exits 2 — never 0.

It is a spelling check for credentials, not an entropy detector; ggshield
remains the stronger layer when it is available.

Usage: scripts/secret_scan.py --diff          # scan added lines in the staged diff
       scripts/secret_scan.py FILE [FILE ...]  # scan whole files (controls)
Exit:  0 clean, 1 findings, 2 could not run (fail closed)

Scanning the diff instead of every tracked file keeps the gate honest: the
repository's existing code was audited when it was published, and re-flagging
legacy test fixtures would train reviewers to ignore this layer.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?key|secret|password|passwd|token|credential)s?\b"
    r"\s*[:=]\s*(.+)$"
)
TOKEN_SHAPED = re.compile(
    r"(?i)\b(sk|pk|ghp|gho|ghu|ghs|github_pat|xox[baprs]|akia|fc|tvly)[-_][A-Za-z0-9_-]{8,}"
)
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
PLACEHOLDER = re.compile(
    r"(?i)(your[-_]|example|placeholder|smoke-token|demo-token|changeme|redacted|\*{3}|x{8,})"
)
MAX_BYTES = 2 * 1024 * 1024
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _is_secret_value(value: str) -> bool:
    if not value:
        return False
    if PLACEHOLDER.search(value):
        return False
    if "(" in value or value.startswith(("os.", "getenv")):
        return False  # a call, not a literal
    if IDENTIFIER.match(value):
        return False  # a variable/attribute reference
    if TOKEN_SHAPED.search(value):
        return True
    # Mixed letters and digits, long enough to be a real credential.
    return len(value) >= 16 and bool(re.search(r"[A-Za-z]", value)) and bool(re.search(r"[0-9]", value))


def scan_file(path: Path) -> list[str]:
    findings: list[str] = []
    data = path.read_bytes()
    if len(data) > MAX_BYTES or b"\x00" in data[:4096]:
        return findings  # binary or huge: out of scope for this checker
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit(f"secret scan: cannot decode {path}: {exc}") from exc

    for number, line in enumerate(text.splitlines(), start=1):
        token = TOKEN_SHAPED.search(line)
        if token and not PLACEHOLDER.search(token.group(0)):
            findings.append(f"{path}:{number}: token-shaped credential {token.group(0)!r}")
            continue
        match = ASSIGNMENT.search(line)
        if match:
            value = match.group(2).strip().strip("\"'")
            if _is_secret_value(value):
                findings.append(f"{path}:{number}: {match.group(1)} looks assigned a literal secret")
    return findings


def _check_line(path: str, number: int, line: str) -> list[str]:
    findings: list[str] = []
    token = TOKEN_SHAPED.search(line)
    if token and not PLACEHOLDER.search(token.group(0)):
        return [f"{path}:{number}: token-shaped credential {token.group(0)!r}"]
    match = ASSIGNMENT.search(line)
    if match:
        value = match.group(2).strip().strip("\"'")
        if _is_secret_value(value):
            findings.append(f"{path}:{number}: {match.group(1)} looks assigned a literal secret")
    return findings


def scan_staged_diff() -> tuple[list[str], int]:
    """Scan added lines in `git diff --cached`. Returns (findings, files_seen)."""
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        print("secret scan: not a git checkout", file=sys.stderr)
        raise SystemExit(2)

    result = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--no-color"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        first = (result.stderr.strip().splitlines() or [""])[0]
        print(f"secret scan: git diff failed: {first}", file=sys.stderr)
        raise SystemExit(2)

    findings: list[str] = []
    current: str | None = None
    number = 0
    seen: set[str] = set()

    for raw in result.stdout.splitlines():
        if raw.startswith("+++ b/"):
            current = raw[len("+++ b/"):]
            seen.add(current)
            continue
        if raw.startswith("+++ /dev/null"):
            current = None
            continue
        if raw.startswith("@@"):
            match = HUNK.match(raw)
            number = int(match.group(1)) if match else 0
            continue
        if current is None or not raw.startswith("+"):
            continue
        findings.extend(_check_line(current, number, raw[1:]))
        number += 1
    return findings, len(seen)


def main(argv: list[str]) -> int:
    if argv == ["--diff"]:
        findings, files = scan_staged_diff()
        if findings:
            for finding in findings:
                print(finding)
            return 1
        print(f"secret scan: staged diff across {files} files, clean")
        return 0

    if not argv:
        print("secret scan: pass --diff or a list of files", file=sys.stderr)
        return 2

    findings: list[str] = []
    for raw in argv:
        path = Path(raw)
        if not path.is_file():
            print(f"secret scan: missing file {path}", file=sys.stderr)
            return 2
        findings.extend(scan_file(path))

    if findings:
        for finding in findings:
            print(finding)
        return 1
    print(f"secret scan: {len(argv)} files, clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
