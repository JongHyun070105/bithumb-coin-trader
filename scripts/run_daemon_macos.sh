#!/usr/bin/env bash
set -euo pipefail
umask 077

# ─────────────────────────────────────────────────────────────────────────────
# 🤖 Bithumb Coin Trader — macOS Native Daemon Wrapper
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CANONICAL_RUNTIME_DIR="$HOME/Library/Application Support/BithumbCoinTrader"
if [ "$PROJECT_DIR" != "$CANONICAL_RUNTIME_DIR" ]; then
    echo "Direct repository daemon execution is disabled; use scripts/service_start.sh" >&2
    exit 1
fi
cd "$PROJECT_DIR"

# 1. 로컬 환경변수 파일 로드
ENV_FILE="$PROJECT_DIR/.env.local"
if [ ! -f "$ENV_FILE" ]; then
    echo "Missing $ENV_FILE" >&2
    exit 1
fi
if [ "$(stat -f '%Su' "$ENV_FILE")" != "$(id -un)" ] || [ "$(stat -f '%Lp' "$ENV_FILE")" != "600" ]; then
    echo ".env.local must be owned by $(id -un) with mode 0600" >&2
    exit 1
fi
# shellcheck disable=SC1091
source "$ENV_FILE"

# 2. PATH 환경변수 설정 (Node, Homebrew, 기본 시스템 경로)
export PATH="/Users/macintosh/.nvm/versions/node/v23.6.0/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PYTHONPATH="$PROJECT_DIR/src:$PROJECT_DIR"

# 3. 트레이딩 환경변수 설정
export PYTHONUNBUFFERED=1
export BITHUMB_LIVE_TRADING=true
export TRADING_MODE=live
export BITHUMB_NEW_ENTRIES=false

# 4. 로그 디렉토리 생성
mkdir -p "$PROJECT_DIR/logs"
rotate_log() {
    local path="$1"
    if [ -f "$path" ] && [ "$(stat -f '%z' "$path")" -ge 5242880 ]; then
        mv -f "$path" "$path.1"
    fi
}
rotate_log "$PROJECT_DIR/logs/daemon.log"
rotate_log "$PROJECT_DIR/logs/daemon_error.log"
exec >>"$PROJECT_DIR/logs/daemon.log" 2>>"$PROJECT_DIR/logs/daemon_error.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🚀 Starting Bithumb Autonomous Trader macOS Daemon..."

# 5. caffeinate를 감싸 실행하여 macOS가 잠자기에 들어가지 않도록 보호하면서 데몬 구동
exec caffeinate -i -s -m /opt/homebrew/bin/python3 "$PROJECT_DIR/scripts/autonomous_trader.py"
