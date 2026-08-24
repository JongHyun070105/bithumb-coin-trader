#!/usr/bin/env bash
set -eo pipefail
umask 077

# ─────────────────────────────────────────────────────────────────────────────
# 🤖 Bithumb Coin Trader — macOS Native Daemon Wrapper
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_DIR="/Users/macintosh/Documents/ChatGPT/bitcoin-trader"
cd "$PROJECT_DIR"

# 1. 로컬 환경변수 파일 로드
if [ -f "$PROJECT_DIR/.env.local" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.env.local"
fi

# 2. PATH 환경변수 설정 (NVM Node, Homebrew, 기본 시스템 경로)
export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
    # shellcheck disable=SC1090
    source "$NVM_DIR/nvm.sh"
fi

export PATH="/Users/macintosh/.nvm/versions/node/v23.6.0/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# 3. 트레이딩 환경변수 설정
export PYTHONUNBUFFERED=1
export BITHUMB_LIVE_TRADING=true
export TRADING_MODE=live

# 4. 로그 디렉토리 생성
mkdir -p "$PROJECT_DIR/logs"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🚀 Starting Bithumb Autonomous Trader macOS Daemon..."

# 5. caffeinate를 감싸 실행하여 macOS가 잠자기에 들어가지 않도록 보호하면서 데몬 구동
exec caffeinate -i -s -m "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/autonomous_trader.py"
