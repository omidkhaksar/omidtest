#!/bin/sh
set -e

PORT="${PORT:-8000}"

case "${RUN_MODE:-all}" in
  api)
    exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
    ;;
  bot)
    exec python -m app.bot
    ;;
  *)
    exec python run.py
    ;;
esac
