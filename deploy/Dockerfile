# syntax=docker/dockerfile:1
#
# Olisar server image — the same unified backend (API + Discord bot + dashboard) the
# desktop app runs, packaged to run headless 24/7 on a small Linux VM (e.g. an Oracle
# Cloud Free Tier ARM instance). Public access is via the bundled Tailscale Funnel
# sidecar (set TAILSCALE_AUTH). Build context is the repo root:
#   docker build -f deploy/Dockerfile -t olisar .

# 1) Build the dashboard (served same-origin by FastAPI at "/").
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# 2) Build the Tailscale Funnel sidecar (reuse the existing tsnet Go source).
FROM golang:1.26 AS funnel
WORKDIR /src
COPY desktop/funnel-sidecar/ ./
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -o /out/olisar-funnel .

# 3) Resolve Python dependencies into a venv (build-essential is for the quickjs C ext;
#    sqlite-vec / cryptography / lxml ship prebuilt aarch64 wheels).
FROM python:3.13-slim AS deps
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --all-extras --no-install-project

# 4) Runtime image.
FROM python:3.13-slim
RUN useradd -m -u 10001 olisar
WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY --from=funnel /out/olisar-funnel /app/olisar-funnel
# Source for `python -m olisar.runtime` (run from source — not PyInstaller-frozen here).
COPY olisar/ ./olisar/
COPY api/ ./api/
COPY bot/ ./bot/
COPY scripts/ ./scripts/
COPY pyproject.toml ./
COPY --from=web /web/dist ./web/dist
ENV PATH="/app/.venv/bin:$PATH" \
    OLISAR_FUNNEL=/app/olisar-funnel \
    OLISAR_DATA_DIR=/var/lib/olisar \
    OLISAR_HOST=0.0.0.0 \
    OLISAR_PORT=8000 \
    OLISAR_HEADLESS=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1
RUN mkdir -p /var/lib/olisar && chown olisar:olisar /var/lib/olisar
USER olisar
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3).status==200 else 1)"
CMD ["python", "-m", "olisar.runtime", "--host", "0.0.0.0", "--port", "8000"]
