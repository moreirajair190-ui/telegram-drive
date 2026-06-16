#!/usr/bin/env bash
# Inicia o backend web do TgPlayer (FastAPI + Uvicorn).
# Uso: bash web/run_web.sh
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src:$PWD"
PORT="${TGWEB_PORT:-8800}"
echo "🚀 TgPlayer Web em http://localhost:${PORT}"
exec python3 -m uvicorn web.backend.main:app --host "${TGWEB_HOST:-0.0.0.0}" --port "${PORT}" "$@"
