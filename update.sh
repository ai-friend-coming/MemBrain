#!/usr/bin/env bash
set -euo pipefail

# 一键更新脚本：拉取远端 membrain-api 镜像，并按当前容器镜像 ID 决定启动、替换或重启。
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.yml}"
SERVICE="${MEMBRAIN_SERVICE:-membrain-api}"
DEFAULT_IMAGE="ghcr.io/ai-friend-coming/membrain-api:main"

die() {
  echo "Error: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "$1 not found"
}

read_env_value() {
  local key="$1"
  local file="$2"
  local line
  line="$(grep -E "^${key}=" "$file" | tail -n 1 || true)"
  if [[ -z "$line" ]]; then
    return 0
  fi
  line="${line#*=}"
  line="${line%\"}"
  line="${line#\"}"
  line="${line%\'}"
  line="${line#\'}"
  printf '%s' "$line"
}

compose() {
  MEMBRAIN_ENV_FILE="$ENV_FILE" docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

need_cmd docker
docker compose version >/dev/null 2>&1 || die "docker compose plugin not available"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found; copy .env.example.deploy to .env first"

MEMBRAIN_IMAGE="${MEMBRAIN_IMAGE:-$(read_env_value MEMBRAIN_IMAGE "$ENV_FILE")}"
MEMBRAIN_IMAGE="${MEMBRAIN_IMAGE:-$DEFAULT_IMAGE}"

echo "Using image: $MEMBRAIN_IMAGE"
echo "Pulling latest image..."
compose pull "$SERVICE"

new_image_id="$(docker image inspect "$MEMBRAIN_IMAGE" --format '{{.Id}}' 2>/dev/null || true)"
[[ -n "$new_image_id" ]] || die "pulled image not found locally: $MEMBRAIN_IMAGE"

container_id="$(compose ps -q "$SERVICE" 2>/dev/null || true)"
if [[ -z "$container_id" ]]; then
  echo "Service is not running; starting all services..."
  compose up -d
  exit 0
fi

is_running="$(docker inspect "$container_id" --format '{{.State.Running}}' 2>/dev/null || echo false)"
if [[ "$is_running" != "true" ]]; then
  echo "Service container exists but is not running; starting services..."
  compose up -d
  exit 0
fi

current_image_id="$(docker inspect "$container_id" --format '{{.Image}}')"
if [[ "$current_image_id" != "$new_image_id" ]]; then
  echo "New image detected; replacing $SERVICE container..."
  compose rm -sf "$SERVICE"
  compose up -d "$SERVICE"
else
  echo "Image unchanged; restarting $SERVICE..."
  compose restart "$SERVICE"
fi

echo "Done."
