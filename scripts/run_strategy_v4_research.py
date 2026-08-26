"""Run Strategy V4 research."""

import sys
from pathlib import Path
from statistics import mean, pstdev
from math import sqrt

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.strategy_v4_candidates import strategy_v4_candidate_factories
from bithumb_coin_trader.strategy_v3_research import v3_settings, _prefix_audit
from bithumb_coin_trader.rebalance_backtest import RebalanceBacktester

def main():
    repo_root = Path(__file__).resolve().parents[1]
    data_path = repo_root / "data/krw-btc-1d-2026-08-24-2400.csv"
    
    # 1. Load 2400 candles
    candles = load_candles_csv(data_path)
    
    # 2. development = 앞 2220개
    development = candles[:2220]
    
    # 3. direct_source = development[1019:] (1020번째부터)
    direct_source = development[1019:]
    
    factories = strategy_v4_candidate_factories()
    
    for name, factory in factories.items():
        candidate = factory()
        weights = candidate.generate(development)
        
        # 4. 각 전략 가중치 생성 (direct_source에 맞는 weights)
        direct_weights = weights[1019:]
        
        print(f"\n[{name}]")
        
        # 5. RebalanceBacktester(v3_settings(1)).run(...) 실행
        # 6. 비용 1배/3배 스트레스 결과 출력
        for mult in (1, 3):
            settings = v3_settings(mult)
            backtester = RebalanceBacktester(settings)
            result = backtester.run(direct_source, direct_weights)
            
            # calculate returns, mdd, sharpe
            returns = [result.equity_curve[i] / result.equity_curve[i - 1] - 1.0 for i in range(1, len(result.equity_curve))]
            volatility = pstdev(returns) if len(returns) > 1 else 0.0
            sharpe = mean(returns) / volatility * sqrt(365.25) if volatility > 0 else 0.0
            total_return = result.total_return
            mdd = result.max_drawdown
            
            print(f"  Cost x{mult}: Return={total_return:.2%}, MDD={mdd:.2%}, Sharpe={sharpe:.2f}")

        # 7. prefix audit (mismatch 0 확인)
        audit = _prefix_audit(candidate, development, weights)
        print(f"  Prefix Mismatches: {audit['mismatch_count']} (Passed: {audit['passed']})")


if __name__ == "__main__":
    main()
