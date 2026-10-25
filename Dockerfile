# KAGOU server: API, BSC listener, background duties, the legacy console (/) and the KAGOU Console (/terminal/).
# Build:  docker build -t kagou:local .
# Run:    see deploy/app.kagouai.com/ and docs/deploy-kagou.md. Secrets come from the environment, never the image.

FROM node:22-bookworm-slim AS console
WORKDIR /src/web/terminal
COPY web/terminal/package.json web/terminal/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/terminal/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# backend/ reads web/ and config/ relative to the repository, so the app runs from /app (editable install).
COPY pyproject.toml README.md ./
COPY agent/ agent/
COPY backend/ backend/
RUN pip install -e .
COPY config/ config/
COPY web/console.html web/console.html
COPY --from=console /src/web/terminal/dist web/terminal/dist
RUN useradd --system --uid 10001 --home-dir /app kagou && mkdir -p /data && chown kagou /data
USER kagou
ENV CHAINSTAFF_DB=/data/kagou.db \
    CHAINSTAFF_POLICY=/data/policy.json \
    CHAINSTAFF_CHAIN=/app/config/chain.json
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/readyz' % __import__('os').environ.get('PORT', '8000'), timeout=4).status == 200 else 1)"
# The console edits the policy file in place (atomic replace in its folder), so it lives on the data volume. First start
# seeds it from the example (placeholder owner, no live wallet: test mode). Edit it before anyone relies on approvals.
# One worker: the engine, the listener and the background duties are in-process singletons sharing one SQLite file.
# PORT: platforms such as Railway assign one; 8000 otherwise. A data folder that is not writable stops the start with a
# clear message instead of running on a database that cannot persist.
CMD ["sh", "-c", "d=$(dirname \"$CHAINSTAFF_DB\"); touch \"$d/.write-test\" 2>/dev/null && rm -f \"$d/.write-test\" || { echo \"KAGOU: $d is not writable by uid $(id -u); the database would not persist\" >&2; exit 1; }; [ -f \"$CHAINSTAFF_POLICY\" ] || cp config/policy.example.json \"$CHAINSTAFF_POLICY\"; exec uvicorn backend.app:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1 --timeout-graceful-shutdown 10 --proxy-headers --forwarded-allow-ips '*'"]
