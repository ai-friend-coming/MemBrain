#!/usr/bin/env bash
set -euo pipefail

# 一键启动入口：按运行环境选择 Docker API、Mac MLX 或 Linux 本地模型组合。
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROFILE="${MEMBRAIN_PROFILE:-thirdparty}"
ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.yml}"
SCREEN_NAME="${SCREEN_NAME:-membrain-mlx-api}"
LOG_FILE="${LOG_FILE:-/tmp/membrain-mlx-api.log}"

die() {
  echo "Error: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "$1 not found"
}

compose() {
  MEMBRAIN_ENV_FILE="$ENV_FILE" docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

backend_port() {
  awk -F= '/^BACKEND_PORT=/{print $2}' "$ENV_FILE" 2>/dev/null | tail -1
}

ensure_env() {
  [[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found"
}

start_docker_all() {
  ensure_env
  need_cmd docker
  docker compose version >/dev/null 2>&1 || die "docker compose plugin not available"
  compose up -d
}

start_docker_db_only() {
  ensure_env
  need_cmd docker
  docker compose version >/dev/null 2>&1 || die "docker compose plugin not available"
  compose up -d db
}

stop_docker_api_if_present() {
  if compose ps -q membrain-api >/dev/null 2>&1; then
    compose stop membrain-api >/dev/null 2>&1 || true
  fi
}

start_mac_mlx_api() {
  [[ "$(uname -s)" == "Darwin" ]] || die "mac-mlx profile must run on macOS"
  [[ "$(uname -m)" == "arm64" ]] || die "mac-mlx profile requires Apple Silicon"
  need_cmd uv
  need_cmd screen

  start_docker_db_only
  stop_docker_api_if_present

  # 使用 Python 3.11 是因为当前 MLX 依赖链和 psycopg2 wheels 在该版本上最稳。
  UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-.venv-mlx}" \
    uv sync --python 3.11 >/dev/null

  if screen -ls | grep -q "[.]${SCREEN_NAME}[[:space:]]"; then
    screen -S "$SCREEN_NAME" -X quit || true
    sleep 1
  fi

  local port
  port="$(backend_port)"
  port="${port:-8094}"

  screen -dmS "$SCREEN_NAME" zsh -lc \
    "cd '$ROOT_DIR' && MEMBRAIN_ENV_FILE='$ENV_FILE' .venv-mlx/bin/python -m uvicorn membrain.api.server:app --host 127.0.0.1 --port '$port' --workers 1 >> '$LOG_FILE' 2>&1"

  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
      echo "MemBrain mac-mlx is running: http://127.0.0.1:${port}"
      echo "screen session: $SCREEN_NAME"
      echo "log file: $LOG_FILE"
      return
    fi
    sleep 1
  done

  tail -120 "$LOG_FILE" >&2 || true
  die "mac-mlx API did not become healthy"
}

case "$PROFILE" in
  thirdparty)
    # 纯 Docker：MemBrain API 调第三方 LLM / embedding / rerank API。
    start_docker_all
    ;;
  linux-local)
    # Linux 本地模型：embedding/rerank 以 OpenAI-compatible HTTP 服务暴露，MemBrain API 仍跑 Docker。
    start_docker_all
    ;;
  mac-mlx)
    # Mac 本地模型：DB 跑 Docker，MemBrain API 在宿主机通过 MLX/Metal 推理。
    start_mac_mlx_api
    ;;
  *)
    die "unknown MEMBRAIN_PROFILE=$PROFILE; expected thirdparty, linux-local, or mac-mlx"
    ;;
esac
