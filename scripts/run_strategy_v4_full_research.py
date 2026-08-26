import json
import sys
from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.strategy_v4_research import build_strategy_v4_report

def main():
    try:
        candles = load_candles_csv('data/krw-btc-1d-2026-08-24-2400.csv')
    except Exception as e:
        print(f"Error loading candles: {e}")
        return

    try:
        report = build_strategy_v4_report(candles)
    except Exception as e:
        print(f"Error building V4 report: {e}")
        return
        
    print(json.dumps(report, indent=2, default=str)[:5000])  # 일부만 출력

    print('\n=== V4 핵심 결과 ===')
    for row in report['direct_development_diagnostics']:
        print(f"{row['name']}: base={row['base']['total_return']:.2%}, x3={row['cost_x3']['total_return']:.2%}, mdd={row['base']['maximum_drawdown']:.2%}, sharpe={row['base']['sharpe']:.3f}")
    
    print('\n=== nested outer ===')
    no = report['nested_outer']
    print(f"base: {no['base']['total_return']:.2%}, x3: {no['cost_x3']['total_return']:.2%}")
    for f in no['folds']:
        print(f"fold{f['fold']}: {f['total_return']:.2%} ({f.get('selected','')})")    
    
    print('\n=== Fold 병렬 진단 출력 ===')
    for sel in report['nested_outer']['selections']:
        print(f"Fold {sel['fold']}: 선택={sel['selected']}")
        for c in sel['candidates']:
            print(f"  {c['name']}: eligible={c['eligible']}, base={c['base_return']:.2%}, score={c.get('selection_score')}")
    
    print('\n=== 게이트 ===')
    for k, v in report['finalist_gates']['checks'].items():
        print(f'  {k}: {v}')
    print(f"all_passed: {report['finalist_gates']['all_passed']}")
    print(f"\n최종 선택: {report['selection']['research_finalist']}")

if __name__ == '__main__':
    main()
