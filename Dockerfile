# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

# 安装运行期依赖：libpq 供 psycopg2 使用，curl 供 Docker healthcheck 使用。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.31 /uv /uvx /usr/local/bin/

# 先安装依赖，最大化利用 Docker layer cache。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 只复制 MemBrain API 运行所需代码和 agent manifests。
COPY membrain ./membrain
COPY evaluation ./evaluation
COPY manifests ./manifests

RUN uv sync --frozen --no-dev

ENV BACKEND_HOST=0.0.0.0 \
    BACKEND_PORT=8094 \
    BACKEND_MODE=demo

EXPOSE 8094

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${BACKEND_PORT}/health" || exit 1

# 使用 shell 入口让 BACKEND_* 环境变量在容器运行时生效。
CMD ["sh", "-c", "uvicorn membrain.api.server:app --host ${BACKEND_HOST:-0.0.0.0} --port ${BACKEND_PORT:-8094} --workers ${BACKEND_WORKERS:-1}"]
