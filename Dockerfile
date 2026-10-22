# syntax=docker/dockerfile:1.7
#
# lit-acquisition service image.
#
# Two build stages (Rust binary, Python environment) and a slim runtime that
# contains only the compiled server, its Python worker environment and the
# CA bundle. Provider credentials are NOT baked in: pass them at run time with
# `--env-file .env` (or compose), which the service loads into the workers.

# Base images are pinned by manifest-list digest so a rebuild resolves the
# same bits on amd64 and arm64. Bump the digest together with the version tag.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
ARG RUST_IMAGE=rust:1.95-slim-bookworm@sha256:d7482085ff5b415f84dba5647ae71606650bdef00db7aeb69f4b3d170c3e4082
ARG UV_VERSION=0.11.28

# ── Stage 1: compile the axum service ──────────────────────────────────────
FROM ${RUST_IMAGE} AS server-build
# crates.io can be slow behind a proxy; retry instead of failing the build.
ENV CARGO_NET_RETRY=10 \
    CARGO_HTTP_TIMEOUT=120 \
    CARGO_TERM_COLOR=never
WORKDIR /build
COPY server ./server
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/server/target \
    cargo build --release --locked --manifest-path server/Cargo.toml \
 && install -m 0755 server/target/release/lit-acquisition-server /usr/local/bin/lit-acquisition-server

# ── Stage 2: build the Python environment (worker side) ────────────────────
FROM ${PYTHON_IMAGE} AS python-build
ARG UV_VERSION
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
WORKDIR /build
# Metadata first so dependency installation is cached independently of source edits.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra agent --no-editable

# ── Stage 3: runtime ───────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime

ARG VERSION=0.5.0
LABEL org.opencontainers.image.title="lit-acquisition" \
      org.opencontainers.image.description="Multilingual biomedical literature acquisition service (axum + Python workers)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.vendor="lit-acquisition maintainers" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/lanshi17/lit-acquisition"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIT_SERVICE_ADDR=0.0.0.0:8080 \
    LIT_SERVICE_PYTHON=/opt/venv/bin/python \
    LIT_SERVICE_WORKERS=2 \
    LIT_SERVICE_REQUEST_TIMEOUT=180 \
    LIT_WORKER_LOG_LEVEL=INFO

# ca-certificates for provider HTTPS; the rest of the toolchain stays out.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /app && chown app:app /app

COPY --from=python-build /opt/venv /opt/venv
COPY --from=server-build /usr/local/bin/lit-acquisition-server /usr/local/bin/lit-acquisition-server
# MIT requires the notice to travel with the distributed binary.
COPY --chown=app:app LICENSE /app/LICENSE

USER app
WORKDIR /app
EXPOSE 8080
STOPSIGNAL SIGTERM

# Probes stay unauthenticated even when LIT_SERVICE_API_KEY is set, so this
# healthcheck keeps working in a locked-down deployment.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; addr=os.getenv('LIT_SERVICE_ADDR','0.0.0.0:8080'); port=addr.rsplit(':',1)[-1]; urllib.request.urlopen('http://127.0.0.1:'+port+'/healthz', timeout=3)"

ENTRYPOINT ["/usr/local/bin/lit-acquisition-server"]
