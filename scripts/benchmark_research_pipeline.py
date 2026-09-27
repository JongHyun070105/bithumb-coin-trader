#!/usr/bin/env python3
"""Synthetic throughput benchmark for the governed research and PAPER paths.

This creates temporary development fixtures only. It does not read exchange,
cloud, holdout, or project evidence and does not start a feed or PAPER process.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.paper_journal import PaperEventJournal
from bithumb_coin_trader.paper_runtime import PaperRuntime
from bithumb_coin_trader.research_infra.backtesting import SpotResearchBacktester
from bithumb_coin_trader.research_infra.batch import BatchExperiment, run_research_batch
from bithumb_coin_trader.research_infra.candidate_freeze import freeze_candidate_experiment
from bithumb_coin_trader.research_infra.costs import SpotCostScenario
from bithumb_coin_trader.research_infra.walk_forward_runner import run_walk_forward
from bithumb_coin_trader.risk_engine import RiskEngine, RiskEngineConfig

from tests.research_infra.test_candidate_freeze import _prepare_selected_experiment
from tests.test_paper_runtime import _candles as _warmup_candles
from tests.test_paper_runtime import _event as _paper_event
from tests.test_paper_runtime import _runtime as _make_paper_runtime
from tests.test_paper_runtime import _scenario as _paper_scenario


class _Fitted:
    def __init__(self, train_mean: float) -> None:
        self.train_mean = train_mean

    def parameters(self) -> Mapping[str, Any]:
        return {"train_mean": self.train_mean}

    def target_weight(self, point_in_time_history: Sequence[Candle]) -> float:
        if not point_in_time_history:
            return 0.0
        return 0.5 if point_in_time_history[-1].close >= self.train_mean else 0.0


class _TrainOnly:
    def fit(self, training_candles: Sequence[Candle]) -> _Fitted:
        if not training_candles:
            raise ValueError("training partition cannot be empty")
        return _Fitted(sum(candle.close for candle in training_candles) / len(training_candles))


class _CrashAfterAccounting(BaseException):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--candles", type=int, default=1_000)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.batch_size <= 0 or args.candles < 50 or args.folds < 2:
        parser.error("require batch-size > 0, candles >= 50, and folds >= 2")
    if args.batch_size > 100 or args.batch_size * args.folds * 2 > 2_000:
        parser.error("synthetic benchmark grid exceeds the bounded 100-experiment/2,000-cell limit")

    candles = _candles(args.candles)
    dataset_sha = hashlib.sha256(_canonical([_candle_dict(candle) for candle in candles])).hexdigest()
    revision = _git_head()
    scenarios = _cost_scenarios()
    weights = [0.5 if index % 10 < 5 else 0.0 for index in range(len(candles))]

    start = time.perf_counter()
    SpotResearchBacktester().run(candles, weights, cost_scenario=scenarios[0])
    backtest_s = time.perf_counter() - start

    start = time.perf_counter()
    run_walk_forward(
        candles,
        lambda _seed: _TrainOnly(),
        scenarios,
        dataset_id="synthetic-benchmark",
        dataset_sha256=dataset_sha,
        code_revision=revision,
        candidate_family="benchmark",
        strategy_id="benchmark_train_only",
        strategy_config={},
        feature_config={"input": "completed_candle_history"},
        n_folds=args.folds,
        window_mode="EXPANDING",
        purge_s=0,
        embargo_s=0,
        seed=7,
    )
    walk_forward_s = time.perf_counter() - start

    with tempfile.TemporaryDirectory(prefix="research-pipeline-benchmark-") as temporary:
        output_root = Path(temporary)
        experiments = tuple(
            BatchExperiment(
                candidate_family="benchmark",
                strategy_id=f"benchmark_strategy_{index:03d}",
                strategy_factory=lambda _seed, _params: _TrainOnly(),
                strategy_config={"variant": index},
                feature_config={"input": "completed_candle_history"},
                parameter_sets=({},),
                seed=7,
            )
            for index in range(args.batch_size)
        )
        start = time.perf_counter()
        batch = run_research_batch(
            candles=candles,
            dataset_id="synthetic-benchmark",
            dataset_sha256=dataset_sha,
            code_revision=revision,
            dataset_provenance={"role": "SYNTHETIC_BENCHMARK_ONLY"},
            experiments=experiments,
            cost_scenarios=scenarios,
            output_dir=output_root,
            n_folds=args.folds,
            window_mode="EXPANDING",
            purge_s=0,
            embargo_s=0,
            max_experiments=max(args.batch_size, 1),
            max_fold_cost_runs=max(args.batch_size * args.folds * len(scenarios), 1),
        )
        batch_s = time.perf_counter() - start

        paper_recovery_s = _bench_paper_recovery(output_root / "paper-recovery")

    report = {
        "schema_version": 1,
        "status": "SYNTHETIC_BENCHMARK_ONLY",
        "code_revision": revision,
        "candle_count": len(candles),
        "batch_size": args.batch_size,
        "folds": args.folds,
        "cost_scenario_count": len(scenarios),
        "single_backtest_seconds": backtest_s,
        "single_walk_forward_seconds": walk_forward_s,
        "batch_seconds": batch_s,
        "experiments_per_second": args.batch_size / batch_s if batch_s > 0 else None,
        "paper_crash_recovery_seconds": paper_recovery_s,
        "batch_status": batch["status"],
        "batch_completed": batch["completed_count"],
        "batch_experiments": batch["experiment_count"],
        "input_provenance": "synthetic only; no 30H, holdout, exchange, or cloud data",
    }
    encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
        print(f"Benchmark report: {output}")
    return 0 if batch["status"] == "COMPLETE" else 1


def _bench_paper_recovery(database_path: Path) -> float:
    with tempfile.TemporaryDirectory(prefix="paper-runtime-recovery-benchmark-") as temporary:
        temp_root = Path(temporary)
        research_root, registry_path, candidate_id, _attempt_dir = _prepare_selected_experiment(temp_root)
        artifact = freeze_candidate_experiment(
            experiment_id=candidate_id,
            candidate_id=candidate_id,
            research_root=research_root,
            candidate_registry_path=registry_path,
            output_path=research_root / "frozen-candidates" / f"{candidate_id}.json",
        )
        warmup = _warmup_candles()
        runtime = _make_paper_runtime((research_root, artifact, warmup), database_path)
        event = _paper_event(warmup, "paper-benchmark-recovery")
        original_apply = runtime._journal.apply_execution_result

        def crash_after_accounting(*args: Any, **kwargs: Any) -> bool:
            original_apply(*args, **kwargs)
            raise _CrashAfterAccounting

        # The execution/accounting transaction has committed before the final
        # event/state journal write; this is the intended replay checkpoint.
        runtime._journal.apply_execution_result = crash_after_accounting  # type: ignore[method-assign]
        try:
            runtime.process_event(event)
        except _CrashAfterAccounting:
            pass
        else:
            raise RuntimeError("benchmark crash injection did not fire")

        start = time.perf_counter()
        recovered = PaperRuntime.from_frozen_artifact(
            candidate_artifact=artifact,
            research_root=research_root,
            warmup_candles=warmup,
            cost_scenario=_paper_scenario(),
            accept_depth_partials=True,
            journal=PaperEventJournal(database_path),
            risk_engine=RiskEngine(RiskEngineConfig(
                max_order_notional_krw=10_000_000.0,
                max_portfolio_exposure_fraction=1.0,
                max_spread_bps=50.0,
                max_slippage_bps=30.0,
                max_data_age_ms=5_000.0,
            )),
            initial_cash_krw=Decimal("1000000"),
            market="KRW-BTC",
        )
        if recovered._journal.get_runtime_event(event.event_id) is None:
            raise RuntimeError("paper benchmark replay did not commit the interrupted runtime event")
        return time.perf_counter() - start


def _candles(count: int) -> list[Candle]:
    start = datetime(2018, 1, 1, tzinfo=UTC)
    candles: list[Candle] = []
    close = 10_000_000.0
    for index in range(count):
        change = ((index % 23) - 11) * 0.0005
        open_price = close
        close = max(1.0, close * (1 + change))
        candles.append(Candle(
            timestamp=start + timedelta(days=index),
            open=open_price,
            high=max(open_price, close) * 1.001,
            low=min(open_price, close) * 0.999,
            close=close,
            volume=100.0 + index % 7,
            market="KRW-BTC",
        ))
    return candles


def _cost_scenarios() -> tuple[SpotCostScenario, SpotCostScenario]:
    common = {
        "maker_fee_bps": 8.0,
        "minimum_order_notional": 1_000.0,
        "tick_size": 1.0,
        "lot_size": 0.00000001,
        "partial_fill_probability": None,
        "partial_fill_status": "UNSUPPORTED",
    }
    return (
        SpotCostScenario(name="conservative", taker_fee_bps=16.0, slippage_bps=10.0, latency_ms=1.0, **common),
        SpotCostScenario(name="stress", taker_fee_bps=32.0, slippage_bps=30.0, latency_ms=2.0, **common),
    )


def _candle_dict(candle: Candle) -> dict[str, Any]:
    return {
        "timestamp": candle.timestamp.isoformat(),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "market": candle.market,
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _git_head() -> str:
    import subprocess

    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("benchmark requires a Git checkout")
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if status.returncode != 0 or status.stdout.strip():
        raise RuntimeError("benchmark requires a clean checkout so the reported revision is authoritative")
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
