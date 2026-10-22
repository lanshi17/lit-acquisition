# Gate evidence — axum HTTP service entry point

Change: `feat(service)` — an axum HTTP entry point for `lit-acquisition`
(`server/`), the Python JSON-lines worker it drives
(`src/lit_acquisition/service_worker.py`), the `.env.example` template, and the
gate scripts in `scripts/`.

Reproduce everything with one command from the repository root:

```bash
scripts/run-gates.sh          # exits 0 only if every layer passes
GATE_SKIP="mutation smoke" scripts/run-gates.sh   # faster subsets
```

Result of the run recorded below: **GATES: PASS (11 layers run, 0 skipped)**.

## Spec approval

| Item | Status |
|------|--------|
| Approved spec | **not obtained** — this gate stack was run retroactively on an already-approved feature request ("用 axum 添加端口服务入口"; "代码门控通过后可以推送"). The user authorized *gating then pushing*, not a review of this document. |
| Consequence | Treat the acceptance list below as a post-hoc executable restatement of the delivered behaviour, not as a contract the change was written against. Confidence claim is correspondingly lower. |

The acceptance criteria were derived from the tests that ship with the change;
each row names the test that would fail if the behaviour regressed.

## Acceptance criteria → tests

### Service contract (`server/src/routes.rs`, `server/src/config.rs`)

| # | Behaviour | Test |
|---|-----------|------|
| S1 | Blank/whitespace query → 400 `invalid_request` | `routes::tests::blank_query_is_rejected` |
| S2 | `limit` outside 1..=50 → 400 | `routes::tests::limit_out_of_range_is_rejected` |
| S3 | `provider` must match `[a-z0-9_]{1,64}`, normalised to lowercase | `routes::tests::provider_is_validated` |
| S4 | Unknown `literature_types` → 400; duplicates removed case-insensitively | `routes::tests::literature_type_is_validated` |
| S5 | Omitted `limit`/`language` → 10 / `auto` | `routes::tests::language_defaults_to_auto` |
| S6 | Malformed JSON body → 400 `invalid_json` | `routes::tests::malformed_search_body_returns_json_400` |
| S7 | Body over `LIT_SERVICE_MAX_BODY_BYTES` → 413 `payload_too_large` | `routes::tests::oversized_body_returns_413` |
| S8 | Worker cannot start → 502 `worker_error` | `routes::tests::broken_worker_returns_502` |
| S9 | `/healthz` answers without touching a worker | `routes::tests::healthz_does_not_need_a_worker` |
| S10 | Auth on → 401 without token, 401 for a same-length wrong token, probes stay open | `routes::tests::api_key_is_enforced_except_on_probes`, `routes::tests::wrong_token_of_same_length_is_rejected` |
| S11 | Config defaults, `PORT`, `ADDR` precedence, invalid values rejected | `config::tests::{defaults_are_sane, port_env_is_honoured, explicit_addr_wins, invalid_port_is_rejected, zero_workers_are_rejected}` |

### Worker contract (`tests/test_service_worker.py`)

| # | Behaviour | Test |
|---|-----------|------|
| W1 | `ping` / `version` / unknown method → `method_not_found` | `test_ping_and_version`, `test_unknown_method_is_reported` |
| W2 | Search validation: empty query, >512 chars, `limit` 0/51/"many", bad language, bad or non-list `literature_types` | `test_search_validation_defaults`, `test_search_validation_rejects_bad_input` |
| W3 | `multilingual` uses the multilingual workflow; default uses the online workflow with compact + gate off + lowercase provider | `test_search_uses_compact_versioned_workflow`, `test_multilingual_search_uses_multilingual_workflow` |
| W4 | Items capped at `limit`, `diagnostics.items_total` kept consistent, short/non-list results untouched | `test_search_truncates_to_limit`, `test_search_keeps_short_result_lists`, `test_truncate_leaves_non_list_items_alone`, `test_truncate_without_diagnostics_dict` |
| W5 | Protocol: blank lines skipped, invalid JSON, non-object, non-object params, unexpected exception → `internal_error` without dying, cancellation propagates | `test_serve_frames_each_request_and_survives_bad_input`, `test_serve_reports_unexpected_errors_without_dying`, `test_serve_once_stops_after_one_frame`, `test_serve_propagates_cancellation` |
| W6 | `--once` and `main()` return 0; `KeyboardInterrupt` → 0 | `test_main_once_reads_stdin_and_writes_stdout`, `test_main_returns_zero_on_keyboard_interrupt` |
| W7 | Real subprocess round-trip keeps stdout to protocol frames only | `test_worker_process_round_trip` |

### Environment template (`tests/test_env_example.py`)

| # | Behaviour | Test |
|---|-----------|------|
| E1 | Every env var read by Python or the Rust service is documented | `test_every_python_env_var_is_documented`, `test_every_service_env_var_is_documented` |
| E2 | Port/auth knobs present | `test_service_port_and_auth_are_documented` |
| E3 | No unknown keys, no filled-in secrets; `.env` ignored, `.env.example` trackable | `test_template_has_no_unknown_variables`, `test_no_secret_value_is_filled_in`, `test_template_is_not_gitignored` |

### Preserved invariants (negative constraints)

| # | Invariant | Evidence |
|---|-----------|----------|
| N1 | Python public API unchanged — no new package exports, no changed signatures | full suite `161 passed`; `service_worker` is a module, not re-exported from `__init__` |
| N2 | Response envelope unchanged (`schema_version` 1.0) | smoke: `schema_version=1.0`; `tests/test_contracts.py` unchanged and green |
| N3 | No new Python runtime dependency | `pyproject.toml` untouched in this change |
| N4 | Service stays opt-in; wheel/sdist unchanged | `pyproject.toml` untouched; `server/` sits outside `[tool.hatch.build.targets.wheel]` |

## Gauntlet layers (final run, command → actual result)

| Layer | Command | Result |
|-------|---------|--------|
| Rust format | `cargo fmt --manifest-path server/Cargo.toml -- --check` | PASS |
| Rust lints | `cargo clippy --manifest-path server/Cargo.toml --all-targets -- -D warnings` | PASS |
| Rust tests | `cargo test --locked --manifest-path server/Cargo.toml` | **16 passed, 0 failed** |
| Python lint | `ruff check src tests --exclude tests/test_rerank_ordering.py` | PASS (see baseline note) |
| Python types | `mypy --python-version 3.12 --follow-imports=skip --ignore-missing-imports src/lit_acquisition/service_worker.py` | PASS (scoped; see limits) |
| Python tests | `pytest -q` (ambient `LIT_LLM_*` unset) | **161 passed** |
| Changed-line coverage | `coverage run --branch -m pytest tests/test_service_worker.py tests/test_env_example.py` + `coverage report --fail-under=100 --include='*/lit_acquisition/service_worker.py'` | **100 % (124 statements, 42 branches, 0 missed)**; `--fail-under=100` exits 2 below target, so this layer can fail |
| Mutation | `scripts/mutation_check.py` (10 mutants) | **10 killed, 0 survived, 0 errors** |
| Mutation runner controls | `scripts/mutation_check.py --control` | runner reported the surviving mutant **and** the bad anchor, i.e. it fails when it should |
| Real execution | `scripts/service-smoke.sh` | PASS: healthz, 401 without token, readyz boots worker, search `success=True items=2 providers=23`, 413 for 4 KB body, worker recycled after SIGKILL (503 → 200), clean SIGTERM |
| Secrets | `ggshield` → fallback `scripts/secret_scan.py --diff` | ggshield is installed but not importable in this environment (`ModuleNotFoundError: ggshield`); fallback scanned the **staged diff, 21 files: clean**. `.env` verified gitignored |
| Supply chain | `cargo audit --file server/Cargo.lock` | **95 crates, 0 advisories** after `event-listener 5.4.1 → 5.4.2` (RUSTSEC-2026-0221, unsound warning, surfaced by the first audit run) |

### Mutation hygiene

* The runner proves its own baseline is green before applying any mutant, so a
  broken build cannot be mistaken for a kill.
* Anchors must appear exactly once; a drifted anchor aborts the run instead of
  mutating nothing.
* Sources are restored in `finally` and verified by SHA-256.
* Controls: a docstring-only mutant (must survive) and a missing anchor (must
  error) — the runner reports both, so its "all killed" result is not vacuous.

### What the mutation run bought

The first mutation pass reported **1 survivor**: replacing the constant-time
comparison with `true` was invisible because the only negative auth test sent
*no* token, which returns early on the length check. That was a real gap, not an
equivalent mutant: `routes::tests::wrong_token_of_same_length_is_rejected` was
added and the mutant is now killed. The gate found a hole; it was fixed by
strengthening the test, not by weakening the mutant.

### Secret-scan controls

| Control | Expectation | Observed |
|---------|-------------|----------|
| Clean staged diff with `sk-your-llm-key` placeholder | pass | pass |
| Staged literal `LIT_LLM_API_KEY=sk-live-…` | fail | fail (`token-shaped credential`) |
| Variable-to-variable assignment (`cfg.llm.api_key = api_key`) | pass | pass |
| Not a git checkout | fail closed | exit 2 |

## Known limits (declared, not hidden)

* **Spec approval was not obtained** before implementation; see the first table.
* **No independent verification** (fresh-context adversarial review) was run.
  The gauntlet shares the author's blind spots.
* **mypy is scoped**: mypy 2.1 refuses the pre-existing PEP 701 f-string at
  `orchestration.py:592`, which aborts any package-wide analysis. Only
  `service_worker.py` is checked, with imports skipped, so inter-module type
  drift is not covered.
* **ggshield did not run** (broken install). The fallback is a narrow
  `KEY = value` pattern scan over the staged diff — it does not detect
  high-entropy secrets without a recognisable key name, and it does not scan
  history.
* **Coverage is Python-only**; the Rust service has no line-coverage layer.
  Behavioural coverage there rests on unit tests plus the end-to-end smoke run.
* **Suite health layer skipped**: `pytest-randomly` is not available, so
  order-dependence was not measured; the Rust tests are deterministic and
  order-independent by construction.
* **No concurrency/load test beyond the smoke script's single-worker recycle**;
  the 2-worker concurrent path was checked manually during development, not in
  the gate.
* **Pre-existing baseline** (unchanged by this work, not fixed — out of scope):
  * `tests/test_config.py::test_configure_partial_update` fails when the host
    environment exports `LIT_LLM_BASE_URL`/`LIT_LLM_MODEL`; the gate unsets them.
  * `tests/test_rerank_ordering.py` carries a `RUF100` violation (unused
    `# noqa: E402`); it is an unrelated untracked file and is excluded from the
    lint layer explicitly, not silently.

## Environment changes made for the gate

| Change | Why | Scope |
|--------|-----|-------|
| `uv pip install coverage` | coverage layer had no tool | local `.venv` only; not in `pyproject.toml` |
| `cargo install cargo-audit` | supply-chain layer had no tool | `~/.cargo/bin` |
| `cargo update -p event-listener` (5.4.1 → 5.4.2) | fixes RUSTSEC-2026-0221 | committed: `server/Cargo.lock` |
