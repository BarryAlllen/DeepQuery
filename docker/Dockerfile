# syntax=docker/dockerfile:1.7
# Build & run image for DeepQuery. Bundles AI CLI tools so the server can spawn them.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Node runtime for npm-distributed CLIs (claude-code, opencode, cursor-agent, ...)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# AI CLI tools + MCP servers (pin versions in production)
RUN npm install -g \
        @anthropic-ai/claude-code \
        @modelcontextprotocol/server-filesystem \
    # && npm install -g opencode-ai \
    # && npm install -g cursor-agent \
    && npm cache clean --force

# uv for fast installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock* README.md ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

# Install runtime deps. Frontend deps not needed in production image.
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Run as non-root: claude CLI refuses --dangerously-skip-permissions under root.
# Also create knowledge volume mount point owned by this user.
RUN useradd --create-home --shell /bin/bash --uid 1000 deepquery \
    && mkdir -p /data/knowledge_base \
    && chown -R deepquery:deepquery /app /data
USER deepquery
ENV HOME=/home/deepquery

EXPOSE 8000
ENV DEEPQUERY_ENV=production \
    DEEPQUERY_HOST=0.0.0.0 \
    DEEPQUERY_PORT=8000 \
    DEEPQUERY_KNOWLEDGE_DIR=/data/knowledge_base \
    DEEPQUERY_DATABASE_URL=sqlite+aiosqlite:////data/deepquery.db \
    DEEPQUERY_MCP_SKIP_PERMISSIONS=true

# Container-level healthcheck for OrbStack / k8s readiness
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# 启动：先跑 alembic 迁移把 schema 对齐到当前代码，再启动 web
CMD ["sh", "-c", "uv run alembic upgrade head && uv run deepquery"]

