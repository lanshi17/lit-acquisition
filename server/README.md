# lit-acquisition HTTP service (axum)

A standalone HTTP entry point for `lit-acquisition`, written in Rust with
[axum](https://github.com/tokio-rs/axum). It binds a TCP port, validates
requests, and executes searches through long-lived Python workers.

The service is **optional**: the Python package keeps working exactly as
before. This crate only adds a network edge for agents and other processes that
prefer HTTP over embedding Python.

## Architecture

```
client ──HTTP──> axum router (Rust)
                   │  validation / auth / timeouts / request ids
                   ▼
             worker pool (N actors)
                   │  JSON line over stdin/stdout
                   ▼
        python -m lit_acquisition.service_worker
                   │
                   ▼
        lit_acquisition.agent.search_literature()
                   │
                   ▼
        18 provider backends (httpx)
```

Why a process bridge instead of PyO3?

* no CPython ABI link, no GIL contention with the async runtime;
* a hung or crashed search can be killed and respawned without restarting the
  HTTP server;
* workers are long-lived, so imports and httpx connection pools are paid once
  per worker, not once per request.

## Layout

| File | Responsibility |
|------|----------------|
| `src/main.rs` | Bind the port, tracing, SIGINT/SIGTERM graceful shutdown |
| `src/config.rs` | Environment configuration and validation |
| `src/routes.rs` | Endpoints, request validation, optional bearer auth |
| `src/bridge.rs` | Worker pool, JSON-lines RPC, timeout/recycle logic |
| `src/error.rs` | Uniform JSON error envelope and status mapping |
| `src/lib.rs` | Library surface used by tests and the binary |

## Run

Requirements: Rust 1.80+ and a Python 3.12+ environment that can import
`lit_acquisition`.

```bash
# 1. Python side (either install the package or point PYTHONPATH at src/)
uv sync

# 2. Build and run (binds 0.0.0.0:8080 by default)
LIT_SERVICE_PYTHON=.venv/bin/python \
LIT_SERVICE_PYTHONPATH=src \
cargo run --release --manifest-path server/Cargo.toml
```

The service loads a local `.env` on startup (via `dotenvy`), so the provider
keys, the bind address and `LIT_SERVICE_*` knobs can live there instead of in
the shell. `.env.example` at the repository root is the commented template:

```bash
cp .env.example .env     # then set LIT_SERVICE_PORT / LIT_SERVICE_API_KEY, etc.
```

Variables already exported in the environment win over the file.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Service metadata and endpoint list |
| `GET` | `/healthz` | Liveness; never touches the worker pool |
| `GET` | `/readyz` | Readiness; round-trips a `ping` through a worker (200/503) |
| `GET` | `/v1/version` | Server version plus Python package/schema versions |
| `POST` | `/v1/search` | Literature search |

### `POST /v1/search`

```bash
curl -s -X POST http://127.0.0.1:8080/v1/search \
  -H 'content-type: application/json' \
  -d '{"query":"MECP2 Rett syndrome","limit":5,"language":"auto"}'
```

| Field | Type | Default | Rules |
|-------|------|---------|-------|
| `query` | string | — | Required, non-empty, at most 512 characters |
| `limit` | integer | 10 | 1–50; maximum number of returned items |
| `language` | string | `auto` | `auto` or `multilingual` |
| `provider` | string | all | Single provider id, `[a-z0-9_]{1,64}` |
| `literature_types` | string[] | `[]` | `case_report`, `sequencing`, `functional` |

Unknown fields are rejected (`400`) so typos do not silently change behaviour.

A successful call returns the Python workflow envelope unchanged
(`schema_version`, `items`, `warnings`, `diagnostics`, `summary`, ...) with
these headers:

The service asks the workflow for compact responses, so `candidate_links` and
`raw` stay empty; `items`, `warnings`, `summary` and `diagnostics` are intact.
`items` is capped at `limit` even though the underlying federated search is
larger; `diagnostics.items_total` matches the returned list, while per-provider
counts still describe the full acquisition, and `summary` reports it.
The full field contract is exported by
`lit_acquisition.response_json_schema()`.

* `x-request-id` — generated per request;
* `x-schema-version` — copied from the envelope;
* `x-service` — `lit-acquisition-server`.

HTTP `200` means the search ran; `success: false` inside the envelope means the
search itself reported a failure (for example no provider could be reached),
matching the Python API semantics. Transport and worker failures use 4xx/5xx.

### Errors

```json
{"error": {"code": "invalid_request", "message": "limit must be between 1 and 50"}}
```

| Status | Code | Cause |
|--------|------|-------|
| 400 | `invalid_json` | Malformed JSON body / wrong content type |
| 400 | `invalid_request` | Validation failure, unknown field, unknown method |
| 401 | `unauthorized` | Missing/invalid bearer token when auth is enabled |
| 413 | `payload_too_large` | Body larger than `LIT_SERVICE_MAX_BODY_BYTES` |
| 502 | `worker_error` | Worker failed to start, crashed, or broke protocol |
| 503 | `worker_unavailable` | Pool is shut down |
| 504 | `worker_timeout` | Search exceeded `LIT_SERVICE_REQUEST_TIMEOUT` |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LIT_SERVICE_ADDR` | `0.0.0.0:8080` | Full bind address; wins over host/port |
| `LIT_SERVICE_HOST` / `LIT_SERVICE_PORT` / `PORT` | `0.0.0.0` / `8080` | Bind address parts |
| `LIT_SERVICE_PYTHON` | `python3` | Interpreter for workers |
| `LIT_SERVICE_PYTHONPATH` | inherited | Prepended to the worker `PYTHONPATH` |
| `LIT_SERVICE_PYTHON_MODULE` | `lit_acquisition.service_worker` | Worker module |
| `LIT_SERVICE_WORKERS` | CPU count, capped at 4 | Concurrent workers |
| `LIT_SERVICE_REQUEST_TIMEOUT` | `180` | Seconds per search RPC |
| `LIT_SERVICE_MAX_BODY_BYTES` | `131072` | Maximum request body |
| `LIT_SERVICE_API_KEY` | unset | Bearer token for `/` and `/v1/*` |
| `RUST_LOG` | `info,tower_http=info` | Tracing filter (e.g. `debug`) |
| `LIT_WORKER_LOG_LEVEL` | `INFO` | Worker log level on stderr |

## Wire protocol

One JSON object per line in both directions. Requests carry a monotonic id;
responses echo it.

```json
{"id":"1","method":"search","params":{"query":"MECP2","limit":5}}
{"id":"1","ok":true,"result":{"success":true,"schema_version":"1.0","items":[]}}
{"id":"2","ok":false,"error":{"code":"method_not_found","message":"unknown method"}}
```

Methods: `ping`, `version`, `search`. The worker writes logs to stderr only;
stdout is reserved for protocol frames. On stdin EOF it closes shared HTTP
clients and exits, which is how graceful shutdown reaches Python.

## Concurrency, timeouts and failure handling

* `LIT_SERVICE_WORKERS` workers consume from one bounded queue
  (capacity `workers × 8`); one worker handles one search at a time.
* A worker is started lazily on its first job. If Python is missing, `/healthz`
  still returns 200 while `/readyz` returns 503 and searches return 502.
* An RPC that exceeds `LIT_SERVICE_REQUEST_TIMEOUT` is killed and mapped to 504;
  the next request spawns a fresh worker.
* A crash or malformed frame recycles the worker and returns 502 for the
  in-flight request only.
* The route deadline is the worker timeout plus five seconds, covering queue
  wait.
* On SIGTERM/SIGINT the listener stops, the queue closes, workers get up to five
  seconds to exit, then children are killed.

## Security notes

* Set `LIT_SERVICE_API_KEY` before exposing the service beyond localhost.
  Probes stay unauthenticated so orchestrators can reach them.
* Terminate TLS at a reverse proxy; this process speaks plain HTTP.
* Requests are passed as JSON arguments to Python — no shell is involved.
* Query length, provider id, body size and worker response size are bounded.

## Container image

The service is packaged as a multi-stage image (Rust builder + Python worker
environment, non-root runtime with a `/healthz` healthcheck). Build and run it
from the repository root:

```bash
docker build -t lit-acquisition:0.1.0 .
docker run --rm -p 8080:8080 --env-file .env lit-acquisition:0.1.0
```

`docker compose up --build` does the same and loads `.env` when present; see
the Docker section of the root README for ports, auth and platform notes.

## Test

```bash
cargo fmt --manifest-path server/Cargo.toml -- --check
cargo test --manifest-path server/Cargo.toml
```

Rust tests cover config parsing, validation, health probes, malformed JSON,
auth, and the 502 path when Python is unavailable. The Python worker has its own
tests in `tests/test_service_worker.py`.

## Out of scope

TLS termination, durable job queues, per-user authorization, rate limiting, and
rewriting provider clients in Rust.
