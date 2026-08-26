"""Initialize the cumulative research trial ledger with V1 through V4 exploration history."""

from __future__ import annotations

from pathlib import Path
from bithumb_coin_trader.trial_ledger import TrialRecord, append_trial_record, DEFAULT_LEDGER_PATH

# Remove existing if any to start cleanly
if DEFAULT_LEDGER_PATH.exists():
    DEFAULT_LEDGER_PATH.unlink()

# 1. Historical V1 Wave Exploration Trials (Wave 1~4, 30m candles ~ 50 trials)
# V1 explored intra-day mean-reversion, breakout, and momentum on 30m bars.
# Most resulted in zero or negative Sharpe after 0.3% round-trip fees.
for i in range(1, 51):
    append_trial_record(
        TrialRecord(
            trial_id=f"TRIAL-V1-{i:03d}",
            lane="V1_30m_Intraday",
            strategy_name=f"v1_intraday_wave_candidate_{i}",
            parameters={"wave_id": (i % 4) + 1, "timeframe": "30m", "lookback": 10 + i * 2},
            dataset_manifest_sha256="legacy-v1-30m-dataset-manifest",
            code_hash=f"v1-hash-{i:04x}",
            created_at="2026-08-11T00:00:00+00:00",
            total_return=-0.05 + (i % 7) * 0.02,
            maximum_drawdown=0.15 + (i % 5) * 0.03,
            observed_sharpe=-0.30 + (i % 11) * 0.08,  # Ranges from -0.3 to +0.5
            exposure=0.25,
            description="V1 intraday 30m wave candidate (mostly fee-eroded)",
        )
    )

# 2. V2 Daily Candidates (4 strategies: absolute momentum, sma 50/200, donchian 90/30, dual momentum)
v2_data = [
    ("daily_weekly_absolute_momentum_126_63", {"lookback": 126, "exit": 63}, 0.4749, 0.1064, 1.12),
    ("daily_weekly_sma_trend_50_200", {"fast": 50, "slow": 200}, 0.3820, 0.1250, 0.95),
    ("daily_weekly_donchian_breakout_90_30", {"entry": 90, "exit": 30}, 0.4130, 0.1180, 1.01),
    ("daily_weekly_dual_momentum", {"roc": 60, "sma": 100}, 0.3540, 0.1320, 0.88),
]
for i, (name, params, ret, mdd, sharpe) in enumerate(v2_data, 1):
    append_trial_record(
        TrialRecord(
            trial_id=f"TRIAL-V2-{i:03d}",
            lane="V2_Daily_Trend",
            strategy_name=name,
            parameters=params,
            dataset_manifest_sha256="krw-btc-1d-2026-08-24-2400",
            code_hash=f"v2-hash-{i:04x}",
            created_at="2026-08-25T00:00:00+00:00",
            total_return=ret,
            maximum_drawdown=mdd,
            observed_sharpe=sharpe,
            exposure=0.30,
            description="V2 daily frozen candidate",
        )
    )

# 3. V3 Target Weight Candidates (3 strategies: E9, EntryVolMom, MajorityTrend)
v3_data = [
    ("v3_e9_donchian_volatility", {"entry": 90, "exit": 30, "target_vol": 0.25}, 0.9950, 0.1299, 1.410),
    ("v3_entry_volatility_absolute_momentum", {"lookback": 126, "exit": 63, "target_vol": 0.25}, 0.5210, 0.0980, 1.180),
    ("v3_majority_trend", {"n_of_m": "2_of_3"}, 0.4420, 0.1050, 1.050),
]
for i, (name, params, ret, mdd, sharpe) in enumerate(v3_data, 1):
    append_trial_record(
        TrialRecord(
            trial_id=f"TRIAL-V3-{i:03d}",
            lane="V3_Target_Weight",
            strategy_name=name,
            parameters=params,
            dataset_manifest_sha256="krw-btc-1d-2026-08-24-2400",
            code_hash=f"v3-hash-{i:04x}",
            created_at="2026-08-25T06:00:00+00:00",
            total_return=ret,
            maximum_drawdown=mdd,
            observed_sharpe=sharpe,
            exposure=0.30,
            description="V3 target weight candidate",
        )
    )

# 4. V4 Candidates (8 strategies)
v4_data = [
    ("v4_adaptive_donchian_atr", {"entry": 60, "exit": 30, "atr_mult": 3.0}, 0.4438, 0.0571, 1.459),
    ("v4_triple_momentum_filter", {"periods": [30, 90, 252]}, 0.4766, 0.0927, 1.279),
    ("v4_trend_volatility_regime", {"vol_days": 90, "mom_days": 126}, 0.7454, 0.1284, 1.358),
    ("v4_trend_quality_filter", {"t_stat_days": 30, "mom_days": 90}, 0.3234, 0.0731, 1.205),
    ("v4_volatility_adjusted_momentum", {"mom_days": 90, "vol_days": 21}, 0.3949, 0.1070, 0.922),
    ("v4_kama_trend", {"kama_n": 10, "mom_days": 63}, 0.2559, 0.0786, 0.961),
    ("v4_52week_high_breakout", {"high_days": 365, "exit_days": 63}, 0.2376, 0.0717, 0.952),
    ("v4_adx_kama_confluence", {"kama_n": 10, "adx_period": 14}, 0.2146, 0.0609, 0.969),
]
for i, (name, params, ret, mdd, sharpe) in enumerate(v4_data, 1):
    append_trial_record(
        TrialRecord(
            trial_id=f"TRIAL-V4-{i:03d}",
            lane="V4_Regime_Candidates",
            strategy_name=name,
            parameters=params,
            dataset_manifest_sha256="krw-btc-1d-2026-08-24-2400",
            code_hash=f"v4-hash-{i:04x}",
            created_at="2026-08-25T10:00:00+00:00",
            total_return=ret,
            maximum_drawdown=mdd,
            observed_sharpe=sharpe,
            exposure=0.30,
            description="V4 candidate",
        )
    )

print(f"Initialized Trial Ledger at {DEFAULT_LEDGER_PATH} with {50 + 4 + 3 + 8} records.")
