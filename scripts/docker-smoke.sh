#!/usr/bin/env bash
# Build the service image and verify the running container end to end.
#
# Checks: non-root user, healthcheck, /healthz, /readyz (Python worker import),
# a real search returning the versioned envelope, no baked-in .env, and a clean
# SIGTERM shutdown. Fails closed at the first unexpected result.
#
# Usage: scripts/docker-smoke.sh
# Env:   IMAGE (tag to build), PORT (host port), SKIP_BUILD=1 to reuse a tag.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

IMAGE=${IMAGE:-lit-acquisition:smoke}
PORT=${PORT:-18093}
NAME=lit-smoke-$$
SEARCH_JSON=""
SEARCH_CODE=""

fail() { echo "FAIL: $*" >&2; docker logs "$NAME" 2>&1 | tail -20 >&2 || true; exit 1; }

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  [ -n "$SEARCH_JSON" ] && rm -f "$SEARCH_JSON" "$SEARCH_CODE"
}
trap cleanup EXIT

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "docker daemon is not reachable" >&2; exit 1; }

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  echo "==> docker build $IMAGE"
  docker build -q -t "$IMAGE" . || fail "image build failed"
fi

echo "==> run container"
if [ -f .env ]; then
  docker run -d --name "$NAME" -p "127.0.0.1:$PORT:8080" --env-file .env "$IMAGE" >/dev/null
else
  docker run -d --name "$NAME" -p "127.0.0.1:$PORT:8080" "$IMAGE" >/dev/null
fi

for _ in $(seq 1 60); do
  curl -sf -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
  docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null | grep -qx true || fail "container exited before binding"
  sleep 1
done
curl -sf -m 5 "http://127.0.0.1:$PORT/healthz" >/dev/null || fail "/healthz never became reachable"
echo "ok  /healthz"

test "$(docker exec "$NAME" id -u)" = "10001" || fail "container does not run as uid 10001"
echo "ok  non-root user (uid 10001)"

curl -sf -m 120 "http://127.0.0.1:$PORT/readyz" >/dev/null || fail "/readyz failed (worker could not start)"
echo "ok  /readyz boots the Python worker"

SEARCH_JSON=$(mktemp "${TMPDIR:-/tmp}/docker-smoke-search.XXXXXX.json")
SEARCH_CODE=$(mktemp "${TMPDIR:-/tmp}/docker-smoke-code.XXXXXX")
curl -sS -m 180 -o "$SEARCH_JSON" -w '%{http_code}' \
  -X POST "http://127.0.0.1:$PORT/v1/search" \
  -H 'content-type: application/json' \
  -d '{"query":"MECP2 Rett syndrome","limit":1}' > "$SEARCH_CODE"
code=$(cat "$SEARCH_CODE")
[ "$code" = "200" ] || fail "search returned HTTP $code"
python3 - "$SEARCH_JSON" <<'PY' || exit 1
import json
import sys
with open(sys.argv[1]) as handle:
    data = json.load(handle)
assert data.get("schema_version") == "1.0", data.get("schema_version")
assert len(data.get("items") or []) <= 1
print("ok  search envelope:", data.get("schema_version"), "items:", len(data.get("items") or []))
PY

docker exec "$NAME" sh -c 'test ! -e /app/.env' || fail ".env was baked into the image"
echo "ok  no .env in the image"

docker stop -t 20 "$NAME" >/dev/null
status=$(docker inspect -f '{{.State.ExitCode}}' "$NAME")
[ "$status" = "0" ] || fail "graceful stop exited $status"
echo "ok  graceful SIGTERM shutdown"
echo "docker-smoke: PASS"
