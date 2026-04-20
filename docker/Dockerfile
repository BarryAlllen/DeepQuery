# syntax=docker/dockerfile:1.7
# Build & run image for DeepQuery. Bundles AI CLI tools so the server can spawn them.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Node runtime for npm-distributed CLIs (claude-code, opencode, cursor-agent, ...)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# AI CLI tools (pin versions in production)
RUN npm install -g \
        @anthropic-ai/claude-code \
    # && npm install -g opencode-ai \
    # && npm install -g cursor-agent \
    && npm cache clean --force

# uv for fast installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock* README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev || uv sync --no-dev

EXPOSE 8000
ENV DEEPQUERY_ENV=production \
    DEEPQUERY_HOST=0.0.0.0 \
    DEEPQUERY_PORT=8000

CMD ["uv", "run", "deepquery"]
