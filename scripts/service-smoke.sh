#!/usr/bin/env bash
# End-to-end smoke test for the axum service (server/).
#
# Runs the real binary against the real Python worker and exercises the
# contract that unit tests can only approximate: port binding, worker boot,
# envelope shape, auth, body limit, worker recycling and graceful shutdown.
#
# Every check is fail-closed: a missing curl, a service that never binds, or an
# unexpected status code aborts with a nonzero exit.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BIN=${BIN:-server/target/debug/lit-acquisition-server}
PORT=${SMOKE_PORT:-18811}
QUERY=${SMOKE_QUERY:-"MECP2 Rett syndrome"}
TMP=$(mktemp -d "${TMPDIR:-/tmp}/lit-smoke.XXXXXX")
LOG="$TMP/service.log"
PID=""

cleanup() {
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    kill -TERM "$PID" 2>/dev/null || true
    for _ in $(seq 1 20); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
    kill -9 "$PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

[ -x "$BIN" ] || fail "service binary not found at $BIN (run: cargo build --manifest-path server/Cargo.toml)"
command -v curl >/dev/null || fail "curl is required"
command -v python3 >/dev/null || fail "python3 is required"

if (ss -ltn 2>/dev/null || true) | grep -q ":$PORT "; then
  fail "port $PORT is already in use (set SMOKE_PORT)"
fi

LIT_SERVICE_ADDR="127.0.0.1:$PORT" \
LIT_SERVICE_PYTHON="$REPO_ROOT/.venv/bin/python" \
LIT_SERVICE_PYTHONPATH="$REPO_ROOT/src" \
LIT_SERVICE_WORKERS=1 \
LIT_SERVICE_REQUEST_TIMEOUT=120 \
LIT_SERVICE_API_KEY=smoke-token \
LIT_SERVICE_MAX_BODY_BYTES=2048 \
RUST_LOG=warn \
  "$BIN" >"$LOG" 2>&1 &
PID=$!

for _ in $(seq 1 60); do
  curl -sf -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
  kill -0 "$PID" 2>/dev/null || { cat "$LOG" >&2; fail "service exited before binding"; }
  sleep 0.5
done
curl -sf -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null || { cat "$LOG" >&2; fail "healthz never came up"; }
echo "ok  healthz reachable on $PORT"

code=$(curl -sS -m 10 -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/v1/search" \
  -H 'content-type: application/json' -d '{"query":"MECP2"}')
[ "$code" = "401" ] || fail "search without token returned $code, expected 401"
echo "ok  auth rejects missing token (401)"

code=$(curl -sS -m 120 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/readyz")
[ "$code" = "200" ] || { cat "$LOG" >&2; fail "readyz returned $code, expected 200"; }
echo "ok  readyz boots a worker (200)"

curl -sS -m 180 -D "$TMP/headers" -o "$TMP/search.json" -X POST "http://127.0.0.1:$PORT/v1/search" \
  -H 'content-type: application/json' -H 'authorization: Bearer smoke-token' \
  -d "{\"query\":\"$QUERY\",\"limit\":2}" >/dev/null
python3 - "$TMP/search.json" "$TMP/headers" <<'PY'
import json
import sys

payload, headers = sys.argv[1], sys.argv[2]
with open(payload) as handle:
    data = json.load(handle)
raw_headers = open(headers).read().lower()

assert data.get("schema_version") == "1.0", f"schema_version={data.get('schema_version')!r}"
for key in ("success", "items", "warnings", "route", "summary", "diagnostics"):
    assert key in data, f"missing envelope field {key}"
assert data.get("candidate_links") == [], "compact mode must drop candidate_links"
assert data.get("raw") is None, "compact mode must drop raw"
assert len(data["items"]) <= 2, "limit not enforced"
assert data["diagnostics"]["items_total"] == len(data["items"]), "items_total drifted"
assert data["items"] and data["items"][0]["title"], "items should carry titles"
assert "x-request-id:" in raw_headers, "missing x-request-id header"
assert "x-schema-version: 1.0" in raw_headers, "missing x-schema-version header"
print(f"ok  search envelope: success={data['success']} items={len(data['items'])} "
      f"providers={len(data['diagnostics']['providers'])}")
PY

big=$(python3 -c "print('x' * 4000)")
code=$(curl -sS -m 15 -o "$TMP/big.json" -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/v1/search" \
  -H 'content-type: application/json' -H 'authorization: Bearer smoke-token' \
  -d "{\"query\":\"$big\"}")
[ "$code" = "413" ] || fail "oversized body returned $code, expected 413"
grep -q '"payload_too_large"' "$TMP/big.json" || fail "oversized body error code missing"
echo "ok  oversized body rejected (413 payload_too_large)"

worker=$(pgrep -P "$PID" -f 'lit_acquisition.service_worker' | head -1 || true)
[ -n "$worker" ] || fail "no python worker found as a child of the service"
kill -9 "$worker"
sleep 0.5
code=$(curl -sS -m 30 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/readyz")
[ "$code" = "503" ] || fail "readyz after worker kill returned $code, expected 503"
code=$(curl -sS -m 120 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/readyz")
[ "$code" = "200" ] || fail "worker did not respawn (readyz $code)"
echo "ok  worker recycled after SIGKILL (503 -> 200)"

kill -TERM "$PID"
for _ in $(seq 1 20); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
kill -0 "$PID" 2>/dev/null && fail "service did not stop within 10s of SIGTERM"
wait "$PID" 2>/dev/null || true
PID=""
echo "ok  graceful shutdown on SIGTERM"
echo "smoke: PASS"
