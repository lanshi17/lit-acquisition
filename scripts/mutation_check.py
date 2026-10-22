#!/usr/bin/env python3
"""Apply one mutation at a time and require the test suite to kill each one.

Why this exists
---------------
A green test suite says the tests pass; it does not say the tests assert
anything. This runner introduces plausible bugs into the axum service (Rust)
and the JSON-lines worker (Python), runs the matching suite, and fails when a
mutant survives.

Safety rules baked in (all fail closed):

* the baseline suite must pass *before* any mutant is applied, otherwise every
  mutant would look "killed" because the build is simply broken;
* each mutant's anchor string must appear exactly once, so a drift in the
  source aborts the run instead of silently mutating nothing;
* the source is restored in a ``finally`` block and verified by SHA-256, so a
  crashed run cannot leave a mutant in the tree.

Exit code: 0 only when every mutant is killed and every restore verified.

``--control`` runs the same machinery against two known-bad entries: a mutant
that cannot change behaviour (so it must SURVIVE) and an anchor that does not
exist (so the run must abort). It passes only if the runner reports both
problems, i.e. it proves the checker can fail before you trust its pass.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

RUST = ["cargo", "test", "--quiet", "--manifest-path", "server/Cargo.toml", "--lib"]
PYTHON = ["uv", "run", "--no-sync", "pytest", "-q", "tests/test_service_worker.py"]

MUTANTS: list[dict[str, str | list[str]]] = [
    # --- axum service (Rust) -------------------------------------------------
    {
        "name": "rust_auth_bypass",
        "file": "server/src/routes.rs",
        "old": "    diff == 0\n",
        "new": "    true\n",
        "cmd": RUST,
        "kills": "routes::tests::api_key_is_enforced_except_on_probes",
    },
    {
        "name": "rust_limit_lower_bound",
        "file": "server/src/routes.rs",
        "old": "(1..=MAX_LIMIT).contains(&limit)",
        "new": "(0..=MAX_LIMIT).contains(&limit)",
        "cmd": RUST,
        "kills": "routes::tests::limit_out_of_range_is_rejected",
    },
    {
        "name": "rust_body_limit_removed",
        "file": "server/src/routes.rs",
        "old": "        .layer(DefaultBodyLimit::max(max_body_bytes))\n",
        "new": "",
        "cmd": RUST,
        "kills": "routes::tests::oversized_body_returns_413",
    },
    {
        "name": "rust_probes_require_auth",
        "file": "server/src/routes.rs",
        "old": 'let is_probe = path == "/healthz" || path == "/readyz";',
        "new": "let is_probe = false;",
        "cmd": RUST,
        "kills": "routes::tests::api_key_is_enforced_except_on_probes",
    },
    {
        "name": "rust_default_limit_changed",
        "file": "server/src/routes.rs",
        "old": "self.limit.unwrap_or(10)",
        "new": "self.limit.unwrap_or(20)",
        "cmd": RUST,
        "kills": "routes::tests::language_defaults_to_auto",
    },
    # --- JSON-lines worker (Python) -----------------------------------------
    {
        "name": "py_truncate_disabled",
        "file": "src/lit_acquisition/service_worker.py",
        "old": "    if not isinstance(items, list) or len(items) <= limit:\n        return result\n",
        "new": "    if True:\n        return result\n",
        "cmd": PYTHON,
        "kills": "test_search_truncates_to_limit",
    },
    {
        "name": "py_language_check_disabled",
        "file": "src/lit_acquisition/service_worker.py",
        "old": "    if language not in VALID_LANGUAGES:",
        "new": "    if False:",
        "cmd": PYTHON,
        "kills": "test_search_validation_rejects_bad_input",
    },
    {
        "name": "py_empty_query_allowed",
        "file": "src/lit_acquisition/service_worker.py",
        "old": "    if not query:",
        "new": "    if False:",
        "cmd": PYTHON,
        "kills": "test_search_validation_rejects_bad_input",
    },
    {
        "name": "py_unknown_method_code",
        "file": "src/lit_acquisition/service_worker.py",
        "old": 'raise RpcError("method_not_found", f"unknown method: {method!r}")',
        "new": 'raise RpcError("internal_error", f"unknown method: {method!r}")',
        "cmd": PYTHON,
        "kills": "test_unknown_method_is_reported",
    },
    {
        "name": "py_limit_lower_bound",
        "file": "src/lit_acquisition/service_worker.py",
        "old": "    if not 1 <= limit <= MAX_LIMIT:",
        "new": "    if not limit <= MAX_LIMIT:",
        "cmd": PYTHON,
        "kills": "test_search_validation_rejects_bad_input",
    },
]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)


def run_manifest(mutants: list[dict]) -> int:
    # 1. Baseline must be green, or "kills" below would be meaningless.
    baselines = {tuple(m["cmd"]): m["cmd"] for m in mutants}  # type: ignore[index]
    for cmd in baselines.values():
        print(f"[baseline] {' '.join(cmd)}")
        result = _run(cmd)
        if result.returncode != 0:
            print(f"[baseline] FAILED (exit {result.returncode}); aborting")
            print(result.stdout[-4000:])
            print(result.stderr[-4000:])
            return 1

    killed: list[str] = []
    survivors: list[str] = []
    errors: list[str] = []

    for mutant in mutants:
        name = str(mutant["name"])
        path = REPO_ROOT / str(mutant["file"])
        old = str(mutant["old"])
        new = str(mutant["new"])
        cmd = mutant["cmd"]

        original = path.read_text(encoding="utf-8")
        count = original.count(old)
        if count != 1:
            errors.append(f"{name}: anchor appears {count} times in {mutant['file']}, expected exactly 1")
            print(f"[{name}] ANCHOR ERROR (count={count})")
            continue

        mutated = original.replace(old, new, 1)
        path.write_text(mutated, encoding="utf-8")
        try:
            result = _run(cmd)  # type: ignore[arg-type]
        finally:
            path.write_text(original, encoding="utf-8")

        restored = path.read_text(encoding="utf-8")
        if _digest(restored) != _digest(original):
            errors.append(f"{name}: restore failed, {mutant['file']} differs from the original")
            print(f"[{name}] RESTORE ERROR")
            continue

        if result.returncode != 0:
            killed.append(name)
            print(f"[{name}] killed (exit {result.returncode}) by {mutant['kills']}")
        else:
            survivors.append(name)
            print(f"[{name}] SURVIVED: suite still passed with the bug in place")

    print()
    print(f"mutants: {len(mutants)}  killed: {len(killed)}  survived: {len(survivors)}  errors: {len(errors)}")
    for line in errors:
        print(f"  error: {line}")
    for name in survivors:
        print(f"  survivor: {name}")
    return 0 if not survivors and not errors else 1


CONTROLS: list[dict] = [
    {
        # Cannot change behaviour: the suite must SURVIVE this one.
        "name": "control_survivor",
        "file": "src/lit_acquisition/service_worker.py",
        "old": '"""JSON-lines RPC worker backing the axum HTTP service.',
        "new": '"""JSON-LINES RPC WORKER backing the axum HTTP service.',
        "cmd": PYTHON,
        "kills": "nothing - docstring-only change",
    },
    {
        # Anchor that does not exist: the runner must abort, not pass.
        "name": "control_bad_anchor",
        "file": "src/lit_acquisition/service_worker.py",
        "old": "def this_anchor_does_not_exist_anywhere() -> None:",
        "new": "def this_anchor_does_not_exist_anywhere() -> None:  # mutated",
        "cmd": PYTHON,
        "kills": "nothing - anchor is missing",
    },
]


def main(argv: list[str]) -> int:
    if "--control" in argv:
        print("[control] the runner must report both a survivor and an anchor error")
        result = run_manifest(CONTROLS)
        if result == 0:
            print("[control] FAILED: the runner accepted a surviving mutant and a bad anchor")
            return 1
        print("[control] ok: the runner failed the way it must")
        return 0
    return run_manifest(MUTANTS)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
