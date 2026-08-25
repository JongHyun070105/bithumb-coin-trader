#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env.local"

export PYTHONPATH="$PROJECT_DIR/src:$PROJECT_DIR"
export BITHUMB_NEW_ENTRIES=false
if [ -f "$ENV_FILE" ]; then
    DISCORD_LINE="$(grep -E '^BITHUMB_DISCORD_TARGET=discord:[0-9]{6,30}$' "$ENV_FILE" | tail -n 1 || true)"
    if [ -n "$DISCORD_LINE" ]; then
        export BITHUMB_DISCORD_TARGET="${DISCORD_LINE#BITHUMB_DISCORD_TARGET=}"
    fi
fi
unset BITHUMB_ACCESS_KEY BITHUMB_SECRET_KEY GEMINI_API_KEY

exec /opt/homebrew/bin/python3 "$SCRIPT_DIR/run_weekly_research.py"
