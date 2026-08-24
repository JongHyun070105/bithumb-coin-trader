#!/usr/bin/env bash
set -euo pipefail

LABEL="com.bithumb.coin.trader"
echo "🛑 Stopping Bithumb Trader macOS LaunchAgent Service..."
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    echo "❌ LaunchAgent still loaded" >&2
    exit 1
fi
echo "✅ LaunchAgent stopped. Manually started processes are intentionally untouched."
