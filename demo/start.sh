#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Prerequisites ─────────────────────────────────────────────────────────────
for cmd in uv node npm; do
  command -v "$cmd" &>/dev/null || { echo "Error: $cmd not found. Please install it first."; exit 1; }
done

if [[ ! -f .env ]]; then
  echo "Warning: .env not found; using default configuration."
  echo "         Recommended: cp .env.example .env"
fi

# ── Read backend port (for display only) ──────────────────────────────────────
BACKEND_PORT="${BACKEND_PORT:-10413}"
if [[ -f .env ]]; then
  _port=$(grep -E '^BACKEND_PORT=' .env | cut -d= -f2 | tr -d ' "' || true)
  [[ -n "$_port" ]] && BACKEND_PORT="$_port"
fi

# ── Step 1: Build frontend ────────────────────────────────────────────────────
echo "▶ [1/3] Installing frontend dependencies..."
cd web-app
npm ci --prefer-offline --loglevel=error

echo "▶ [2/3] Building frontend..."
npm run build
cd "$SCRIPT_DIR"

# ── Step 2: Install backend dependencies ──────────────────────────────────────
echo "▶ [3/3] Installing backend dependencies..."
uv sync --no-dev --quiet

# ── Step 3: Start backend (also serves frontend static files) ─────────────────
echo ""
echo "✓ Starting — visit http://localhost:${BACKEND_PORT}"
echo "  Stop: Ctrl+C"
echo ""

exec uv run python -m uvicorn src.main:app \
  --host 0.0.0.0 \
  --port "$BACKEND_PORT" \
  --workers 1
