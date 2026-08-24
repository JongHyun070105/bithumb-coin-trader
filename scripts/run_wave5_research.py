#!/usr/bin/env python3
"""Run the validator-gated Wave 5 BTC-only research comparison."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.data import dataset_manifest, load_candles_csv
from bithumb_coin_trader.research import CandidateComparisonReport, ProjectResearchReport
from bithumb_coin_trader.wave5 import (
    WAVE5_CANDIDATE_NAMES,
    WAVE5_RUNNABLE_BTC_CANDIDATES,
    Wave5Config,
    compare_wave5_btc_candidates,
    select_research_candidate,
    wave5_candidate_manifest,
    wave5_candidate_manifest_hash,
)


DEFAULT_INPUT = Path("data/krw-btc-30m-2026-08-14-wave4.csv")
DEFAULT_OUTPUT = Path(".omx/specs/autoresearch-wave5/result.json")


def _report(value: ProjectResearchReport) -> dict[str, Any]:
    trades = [trade for fold in value.folds for trade in fold.result.trades]
    curve_digest = sha256(b"bithumb-coin-trader:wave5-equity:v1\n")
    for point in value.oos_equity_curve:
        curve_digest.update(float(point).hex().encode("ascii"))
        curve_digest.update(b"\n")
    return {
        "candidate_name": value.candidate_name,
        "fold_count": len(value.folds),
        "compounded_return": value.compounded_return,
        "maximum_drawdown": value.maximum_drawdown,
        "mean_sharpe": value.mean_sharpe,
        "trade_count": value.trade_count,
        "weighted_win_rate": value.weighted_win_rate,
        "closed_trade_count": sum(not trade.is_final_liquidation for trade in trades),
        "oos_equity_evidence": {
            "point_count": len(value.oos_equity_curve),
            "initial_equity_krw": value.oos_equity_curve[0],
            "final_equity_krw": value.oos_equity_curve[-1],
            "sha256": curve_digest.hexdigest(),
        },
        "folds": [
            {
                "fold": fold.fold + 1,
                "train": [fold.train_start, fold.train_end],
                "test": [fold.test_start, fold.test_end],
                "initial_equity_krw": fold.result.initial_equity,
                "final_equity_krw": fold.result.final_equity,
                "total_return": fold.result.total_return,
                "maximum_drawdown": fold.result.max_drawdown,
                "trade_count": fold.result.trade_count,
                "win_rate": fold.result.win_rate,
                "exposure": fold.result.exposure,
            }
            for fold in value.folds
        ],
    }


def _by_name(comparison: CandidateComparisonReport) -> dict[str, ProjectResearchReport]:
    return {report.candidate_name: report for report in comparison.candidates}


def build_report(
    candles: Sequence[Any], *, generated_at: datetime | None = None
) -> dict[str, Any]:
    config = Wave5Config()
    sample = tuple(candles[-config.historical_count :])
    if len(sample) != config.historical_count:
        raise ValueError("Wave 5 requires 40,000 completed 30-minute candles")
    markets = sorted({candle.market for candle in sample})
    if markets != ["KRW-BTC"]:
        raise ValueError("the current Wave 5 runner is frozen to KRW-BTC")

    base_settings = TradingSettings()
    stress_settings = TradingSettings(fee_rate=0.005, slippage_bps=10.0)
    base = compare_wave5_btc_candidates(
        sample, settings=base_settings, config=config
    )
    stress = compare_wave5_btc_candidates(
        sample, settings=stress_settings, config=config
    )
    selected, gates = select_research_candidate(base, stress, config)
    base_by_name = _by_name(base)
    stress_by_name = _by_name(stress)
    manifest = wave5_candidate_manifest(config)
    manifest_payload = {
        **manifest,
        "candidate_count": len(WAVE5_CANDIDATE_NAMES),
        "sha256": wave5_candidate_manifest_hash(manifest),
    }
    identity = dataset_manifest(sample)

    return {
        "schema_version": 5,
        "generated_at": (generated_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "dataset": {
            "markets": markets,
            "market_count": len(markets),
            "candle_count": identity.candle_count,
            "start_at": identity.start_at.isoformat() if identity.start_at else None,
            "end_at": identity.end_at.isoformat() if identity.end_at else None,
            "sha256": identity.sha256,
            "source": "public_completed_30m_ohlcv",
        },
        "candidate_manifest": manifest_payload,
        "candidate_availability": {
            "runnable": list(WAVE5_RUNNABLE_BTC_CANDIDATES),
            "unavailable": {
                "cross_sectional_momentum": {
                    "reason": "requires at least three aligned market histories",
                    "observed_market_count": len(markets),
                    "result": None,
                }
            },
        },
        "validation_geometry": {
            "expanding": True,
            "boundaries": [
                {"train": [a, b], "test": [c, d]}
                for a, b, c, d in config.boundaries()
            ],
        },
        "costs": manifest["costs"],
        "candidates": [
            {
                "name": name,
                "base": _report(base_by_name[name]),
                "double_cost_stress": _report(stress_by_name[name]),
                "gate_evaluation": gates[name],
            }
            for name in WAVE5_RUNNABLE_BTC_CANDIDATES
        ],
        "selection": {
            "research_candidate": selected,
            "fallback_to_cash": selected == "cash",
            "automatic_promotion": "forbidden",
            "can_promote": False,
            "paper_or_live_strategy_changed": False,
        },
        "limitations": [
            "Only KRW-BTC history is locally available, so cross-sectional momentum was not evaluated.",
            "All evidence is adaptive historical evidence; no new prospective forward sample is claimed.",
            "Historical LLM scores and order-book imbalance were excluded because replayable point-in-time data is unavailable.",
            "This artifact cannot change paper or live execution settings.",
        ],
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = build_report(load_candles_csv(args.input))
    _write_json(args.output, report)
    selected = report["selection"]["research_candidate"]
    print(f"Wave 5 research artifact: {args.output}")
    print(f"research candidate: {selected}; automatic promotion: forbidden")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
