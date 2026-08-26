#!/usr/bin/env python3
"""Run V4b strategies backtest."""

import math
import sys
from pathlib import Path
from statistics import mean, pstdev

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.rebalance_backtest import RebalanceBacktester
from bithumb_coin_trader.strategy_v3_research import v3_settings
from bithumb_coin_trader.strategy_v4b_candidates import (
    V452WeekHighBreakoutStrategy,
    V4TrendQualityFilterStrategy,
)

def _prefix_audit(candidate, candles, full_weights):
    checkpoints = sorted(set(range(candidate.required_history_bars, len(candles) + 1, 89)) | {len(candles)})
    mismatches = 0
    for end in checkpoints:
        prefix = candidate.generate(candles[:end])
        mismatches += sum(abs(left - right) > 1e-12 for left, right in zip(prefix, full_weights[:end]))
    return mismatches

def calculate_sharpe(equity_curve):
    returns = [equity_curve[i] / equity_curve[i-1] - 1.0 for i in range(1, len(equity_curve))]
    vol = pstdev(returns) if len(returns) > 1 else 0.0
    return mean(returns) / vol * math.sqrt(365.25) if vol > 0 else 0.0

def main():
    print("데이터 로딩 중...")
    # csv 경로는 프로젝트 환경에 맞게
    csv_path = Path(__file__).resolve().parents[1] / "data/krw-btc-1d-2026-08-24-2400.csv"
    candles = load_candles_csv(csv_path)
    development = candles[:2220]
    direct_source = development[1019:]

    candidates = [
        V452WeekHighBreakoutStrategy(),
        V4TrendQualityFilterStrategy(),
    ]

    for candidate in candidates:
        print(f"\n--- 전략: {candidate.name} ---")
        weights = candidate.generate(development)
        
        mismatches = _prefix_audit(candidate, development, weights)
        print(f"Prefix Mismatch (89봉 간격 검사): {mismatches}")
        
        direct_weights = weights[1019:]
        
        result_1x = RebalanceBacktester(v3_settings(1)).run(direct_source, direct_weights)
        sharpe_1x = calculate_sharpe(result_1x.equity_curve)
        print(f"[비용 1배] 수익률: {result_1x.total_return:.2%}, MDD: {result_1x.max_drawdown:.2%}, Sharpe: {sharpe_1x:.4f}")
        
        result_3x = RebalanceBacktester(v3_settings(3)).run(direct_source, direct_weights)
        sharpe_3x = calculate_sharpe(result_3x.equity_curve)
        print(f"[비용 3배] 수익률: {result_3x.total_return:.2%}, MDD: {result_3x.max_drawdown:.2%}, Sharpe: {sharpe_3x:.4f}")

if __name__ == "__main__":
    main()
