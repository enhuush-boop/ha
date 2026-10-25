#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  echo "No .venv found. Run ./scripts/setup_backend.sh first."
  exit 1
fi

# Bind to loopback by default: the demo should not be exposed to the LAN.
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

echo "TAYO API: http://${HOST}:${PORT}"
echo "Interactive docs: http://${HOST}:${PORT}/docs"
echo "The first analysis downloads model weights (~1 GB total) and is slow."
echo "Press Ctrl+C to stop."

exec .venv/bin/uvicorn backend.app.main:app --host "$HOST" --port "$PORT" "$@"
