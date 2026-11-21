# syntax=docker/dockerfile:1
# Build from the repository root: contracts is a local uv dependency.
FROM ghcr.io/astral-sh/uv:0.11.2 AS uv
FROM python:3.12.13-slim-bookworm AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app
COPY contracts/pyproject.toml /app/contracts/pyproject.toml
COPY contracts/src /app/contracts/src
COPY cloud/gateway/pyproject.toml cloud/gateway/uv.lock /app/cloud/gateway/
COPY cloud/gateway/src /app/cloud/gateway/src
WORKDIR /app/cloud/gateway
RUN uv sync --frozen --no-dev --no-editable --no-cache

FROM python:3.12.13-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/cloud/gateway/.venv/bin:$PATH" PORT=9210
RUN groupadd --gid 10001 pcx \
    && useradd --uid 10001 --gid pcx --no-create-home --shell /usr/sbin/nologin pcx \
    && mkdir -p /app/cloud/gateway \
    && chown pcx:pcx /app/cloud/gateway
COPY --from=builder /app/cloud/gateway/.venv /app/cloud/gateway/.venv
WORKDIR /app/cloud/gateway
USER 10001:10001
EXPOSE 9210
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + '/health', timeout=4).close()"]
# __main__ reads Render's PORT; one worker, SIGTERM grace period 20 seconds.
CMD ["python", "-m", "pcx_gateway"]
