# The portal: the built single-page app served by the API that feeds it.
#
# Two stages, because the toolchain that BUILDS the front end has no business
# in the image that runs in production. Node, npm and 200 packages of build
# dependencies are ~400MB of attack surface that exists only to produce three
# static files.

# ── stage 1: build the single-page app ─────────────────────────────────────
FROM node:22-alpine AS spa

WORKDIR /build/frontend

# Dependencies first, and separately, so a change to the application code does
# not re-download the whole of npm on every rebuild.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
# vite writes to ../server/spa, which is outside this directory on purpose:
# it is where the API expects to find it.
RUN npm run build


# ── stage 2: the application ───────────────────────────────────────────────
FROM python:3.12-slim AS app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY tools/ ./tools/
COPY data/ ./data/
COPY assets/ ./assets/
COPY --from=spa /build/server/spa ./server/spa/

# Runs as nobody in particular. The application writes nothing to disk — the
# database holds the state and the content is read-only — so it has no need of
# a writable filesystem or of root.
RUN useradd --create-home --uid 10001 portal && chown -R portal:portal /app
USER portal

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=40s --retries=6 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', \
timeout=2).status == 200 else 1)"

CMD ["uvicorn", "server.api:app", "--host", "0.0.0.0", "--port", "8000"]
