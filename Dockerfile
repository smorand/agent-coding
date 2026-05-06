# =============================================================================
# Multi-stage Dockerfile for agent-code
# =============================================================================
# The runtime stage bundles the agent's full toolchain so the produced
# container can drive a real ticket end-to-end without `make` / `git` /
# `gh` / `uv` / `ripgrep` / `ast-grep` / `pyright` being installed on the
# host.

ARG APP_VERSION=dev

# =============================================================================
# Stage 1: Build dependencies and install package
# =============================================================================
FROM python:3.13-slim AS builder

ARG APP_VERSION
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Pin the runtime version into src/version.py before installing.
RUN printf 'from __future__ import annotations\n__version__: str = "%s"\n__all__ = ["__version__"]\n' "${APP_VERSION}" > src/version.py

RUN uv sync --frozen --no-dev --no-editable

# =============================================================================
# Stage 2: Runtime image with full agent toolchain
# =============================================================================
FROM python:3.13-slim AS runtime

ARG APP_VERSION
LABEL org.opencontainers.image.title="agent-code"
LABEL org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:/usr/local/bin:/usr/bin:/bin"

WORKDIR /app

# Install the toolchain the agent needs at runtime.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        make \
        git \
        ripgrep \
        curl \
        ca-certificates \
        gnupg \
        tini \
    && rm -rf /var/lib/apt/lists/*

# gh (GitHub CLI) - install from the official apt repo.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install --no-install-recommends -y gh \
    && rm -rf /var/lib/apt/lists/*

# uv (also used at runtime when the agent materializes project deps).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Bring in the venv from the builder; install ast-grep + pyright on top.
COPY --from=builder /app/.venv /app/.venv
RUN /app/.venv/bin/pip install --no-cache-dir ast-grep-cli pyright

# Non-root user.
RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid appgroup --shell /bin/false --no-create-home appuser \
    && mkdir -p /workspace \
    && chown -R appuser:appgroup /workspace

USER appuser:appgroup
WORKDIR /workspace

ENTRYPOINT ["tini", "--", "agent-code"]
CMD ["--help"]
