#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "============================================================"
echo "Bithumb Coin Trader — Local Dashboard Demo Runner"
echo "============================================================"

# 1. Build offline TradingSnapshot from synthetic demo fixtures
echo "[1/3] Building offline TradingSnapshot from synthetic FillLedger fixtures..."
PYTHONPATH=src python3 -m bithumb_coin_trader.dashboard_snapshot \
  --ledger examples/dashboard/fills.demo.jsonl \
  --account-state examples/dashboard/account_state.demo.json \
  --marks examples/dashboard/mark_prices.demo.json \
  --equity-history examples/dashboard/equity_history.demo.json \
  --output examples/dashboard/trading_snapshot.demo.json

echo "[2/3] Starting Localhost-Only Read-Only API Server on port 8765..."
PYTHONPATH=src python3 -m bithumb_coin_trader.dashboard_api \
  --snapshot examples/dashboard/trading_snapshot.demo.json \
  --port 8765 \
  --host 127.0.0.1 &
API_PID=$!

cleanup() {
  echo ""
  echo "Shutting down Local API Server (PID: $API_PID)..."
  kill "$API_PID" 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM EXIT

echo "[3/3] Local API Server started (PID: $API_PID) at http://127.0.0.1:8765"
echo ""
echo "To connect from the browser dashboard:"
echo "  1. Run 'cd dashboard && npm run dev'"
echo "  2. Open http://localhost:4177 or http://localhost:5173"
echo "  3. Click '로컬 API 연결' in the top utility bar"
echo ""
echo "Press Ctrl+C to stop the API server."
wait "$API_PID"
