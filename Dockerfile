# syntax=docker/dockerfile:1

FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_NO_INSTALLER_METADATA=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# First, install the workspace dependencies. This is a separate step so that
# later builds can skip it if the dependencies are unchanged.
COPY --parents uv.lock pyproject.toml **/pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-workspace

# Next, install the dispatcher and worker. We need to build two virtual
# environments: an environment with the dispatcher for our final image, and
# an environment with only the worker to bundle into vendor.tar.gz.

COPY --parents dispatch/* worker/* ./

FROM builder AS builder-dispatch
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable \
      --package sprites-claude-managed-agents-dispatch

FROM builder AS builder-worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable \
      --package sprites-claude-managed-agents-worker \
    && tar -czf /vendor.tar.gz -C .venv/lib/python3.*/site-packages .

FROM python:3.14-slim

COPY --from=builder-dispatch /app/.venv /app/.venv
COPY --from=builder-worker /vendor.tar.gz /app/vendor.tar.gz
ENV VENDOR_TAR_PATH=/app/vendor.tar.gz

RUN useradd --system app
USER app

CMD ["/app/.venv/bin/fastapi", "run", \
      "--entrypoint", "sprites_claude_managed_agents_dispatch.main:app", \
      "--host", "0.0.0.0", "--port", "8080"]
