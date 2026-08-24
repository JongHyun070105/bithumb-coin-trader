#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
RUNTIME_DIR="$HOME/Library/Application Support/BithumbCoinTrader"
LABEL="com.bithumb.coin.trader"
SERVICE="gui/$(id -u)/$LABEL"

echo "================================================================================"
echo " 🤖 Bithumb Trader macOS Daemon Status"
echo "================================================================================"
DETAILS="$(launchctl print "$SERVICE" 2>/dev/null || true)"
if [ -z "$DETAILS" ]; then
    echo "⚠️ Service is NOT loaded in launchd"
else
    printf '%s\n' "$DETAILS" | awk '/state =|pid =|last exit code =/{print}'
fi

echo ""
echo "🔍 Running Processes:"
PIDS="$(pgrep -f "$RUNTIME_DIR/scripts/autonomous_trader.py" || true)"
if [ -n "$PIDS" ]; then
    ps -p "$(printf '%s\n' "$PIDS" | paste -sd, -)" -o pid=,etime=,command=
else
    echo "⚠️ No autonomous_trader process running"
fi

echo ""
echo "📜 Recent Logs (Last 25 lines):"
echo "────────────────────────────────────────────────────────────────────────────────"
LOG_FILE="$RUNTIME_DIR/logs/daemon.log"
if [ -f "$LOG_FILE" ]; then
    tail -n 25 "$LOG_FILE"
else
    echo "No log file found at $LOG_FILE"
fi
echo "────────────────────────────────────────────────────────────────────────────────"
