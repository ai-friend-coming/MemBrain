#!/usr/bin/env bash
set -euo pipefail

# 一键停止入口：停止 Mac MLX screen 服务，并按需停止 Docker compose。
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROFILE="${MEMBRAIN_PROFILE:-thirdparty}"
ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.yml}"
SCREEN_NAME="${SCREEN_NAME:-membrain-mlx-api}"

compose() {
  MEMBRAIN_ENV_FILE="$ENV_FILE" docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

if command -v screen >/dev/null 2>&1 && screen -ls | grep -q "[.]${SCREEN_NAME}[[:space:]]"; then
  screen -S "$SCREEN_NAME" -X quit || true
fi

case "$PROFILE" in
  mac-mlx)
    # Mac MLX 模式默认保留数据库；设置 STOP_DB=true 时一起停掉 DB。
    if [[ "${STOP_DB:-false}" == "true" ]]; then
      compose stop db
    fi
    ;;
  thirdparty|linux-local)
    compose down
    ;;
  *)
    echo "Error: unknown MEMBRAIN_PROFILE=$PROFILE" >&2
    exit 1
    ;;
esac
