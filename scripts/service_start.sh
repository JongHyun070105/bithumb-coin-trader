#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
RUNTIME_DIR="$HOME/Library/Application Support/BithumbCoinTrader"
SOURCE_PLIST="$SCRIPT_DIR/com.bithumb.coin.trader.plist"
PLIST="$HOME/Library/LaunchAgents/com.bithumb.coin.trader.plist"
LABEL="com.bithumb.coin.trader"
SERVICE="gui/$(id -u)/$LABEL"
ENV_FILE="$PROJECT_DIR/.env.local"

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ Missing $ENV_FILE" >&2
    exit 1
fi
if [ "$(stat -f '%Su' "$ENV_FILE")" != "$(id -un)" ] || [ "$(stat -f '%Lp' "$ENV_FILE")" != "600" ]; then
    echo "❌ .env.local must be owned by $(id -un) with mode 0600" >&2
    exit 1
fi
if launchctl print "$SERVICE" >/dev/null 2>&1; then
    launchctl bootout "$SERVICE"
    for _ in 1 2 3 4 5; do
        pgrep -f "$RUNTIME_DIR/scripts/autonomous_trader.py" >/dev/null 2>&1 || break
        sleep 1
    done
fi
if pgrep -f "$PROJECT_DIR/scripts/autonomous_trader.py|$RUNTIME_DIR/scripts/autonomous_trader.py" >/dev/null 2>&1; then
    echo "❌ A trader process is already running outside the managed service" >&2
    exit 1
fi

mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"
rsync -a "$PROJECT_DIR/src/" "$RUNTIME_DIR/src/"
rsync -a "$PROJECT_DIR/scripts/" "$RUNTIME_DIR/scripts/"
if [ ! -d "$RUNTIME_DIR/state" ]; then
    cp -R "$PROJECT_DIR/state" "$RUNTIME_DIR/state"
fi
cp "$ENV_FILE" "$RUNTIME_DIR/.env.local"
chmod 600 "$RUNTIME_DIR/.env.local"
chmod 700 "$RUNTIME_DIR/scripts/run_daemon_macos.sh"
for journal in TRADING_JOURNAL.md EVOLUTION_JOURNAL.md; do
    if [ -f "$PROJECT_DIR/$journal" ] && [ ! -f "$RUNTIME_DIR/$journal" ]; then
        cp "$PROJECT_DIR/$journal" "$RUNTIME_DIR/$journal"
    fi
done

mkdir -p "$(dirname "$PLIST")"
sed "s|__PROJECT_DIR__|$RUNTIME_DIR|g" "$SOURCE_PLIST" > "$PLIST"
chmod 600 "$PLIST"
plutil -lint "$PLIST" >/dev/null
echo "🚀 Starting Bithumb Trader macOS LaunchAgent Service..."
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "$SERVICE"
DETAILS="$(launchctl print "$SERVICE" 2>/dev/null || true)"
if [ -z "$DETAILS" ]; then
    echo "❌ LaunchAgent failed to load" >&2
    exit 1
fi
sleep 1
DETAILS="$(launchctl print "$SERVICE" 2>/dev/null || true)"
if ! grep -q "state = running" <<<"$DETAILS"; then
    echo "❌ LaunchAgent loaded but trader process is not running; inspect logs/daemon_error.log" >&2
    exit 1
fi
echo "✅ Service is loaded and trader process is running. New entries default to OFF."
