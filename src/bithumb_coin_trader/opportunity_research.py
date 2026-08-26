"""Profit-first, artifact-gated research isolated from live execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import random
import stat
from typing import Any, Callable, Mapping, Sequence, cast

from .backtest import BacktestResult, Backtester, Trade
from .data import dataset_manifest
from .models import Candle, Signal
from .opportunity_candidates import candidate_factories as opportunity_factories
from .research import registered_candidate_factories
from .strategy import (
    CompletedIntervalStrategy,
    DonchianBreakoutParameters,
    DonchianBreakoutStrategy,
)
from .winrate_research import normalized_settings


SOURCE_DELTA = timedelta(minutes=30)
CandidateFactory = Callable[[], Any]


class HoldoutLedgerExistsError(RuntimeError):
    """Raised when a one-time holdout ledger already exists."""


@dataclass(frozen=True, slots=True)
class OpportunityResearchConfig:
    historical_count: int = 100_000
    development_count: int = 96_000
    initial_train_count: int = 48_000
    development_test_count: int = 8_000
    development_fold_count: int = 6
    sealed_holdout_count: int = 4_000
    maximum_holdout_candidates: int = 3

    def __post_init__(self) -> None:
        values = tuple(asdict(self).values())
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
            raise ValueError("research counts must be positive integers")
        if self.development_count + self.sealed_holdout_count != self.historical_count:
            raise ValueError("development and holdout must cover the full sample")
        if self.initial_train_count + self.development_test_count * self.development_fold_count != self.development_count:
            raise ValueError("development folds must exactly cover development")


class CashControl:
    name = "cash"

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        return [Signal.FLAT] * len(candles)


def candidate_registry() -> tuple[dict[str, CandidateFactory], dict[str, str]]:
    legacy = registered_candidate_factories()
    selected_legacy = {
        "trend_daily_close_above_sma200": legacy["trend_daily_close_above_sma200"],
        "trend_daily_sma50_above_sma200": legacy["trend_daily_sma50_above_sma200"],
        "donchian_daily_20_10_breakout": legacy["donchian_daily_20_10_breakout"],
    }
    factories: dict[str, CandidateFactory] = {"cash": CashControl, **selected_legacy}
    families = {
        "cash": "cash_control",
        "trend_daily_close_above_sma200": "slow_trend",
        "trend_daily_sma50_above_sma200": "slow_trend",
        "donchian_daily_20_10_breakout": "breakout",
    }
    for entry_period in (40, 55, 70):
        for exit_period in (10, 20, 30):
            name = f"profit_donchian_4h_{entry_period}_{exit_period}"
            if (entry_period, exit_period) == (55, 20):
                name = "donchian_4h_55_20_breakout"

            def grid_factory(
                *,
                entry_period: int = entry_period,
                exit_period: int = exit_period,
                name: str = name,
            ) -> CompletedIntervalStrategy:
                strategy = CompletedIntervalStrategy(
                    DonchianBreakoutStrategy(
                        DonchianBreakoutParameters(entry_period, exit_period)
                    ),
                    source_minutes=30,
                    target_minutes=240,
                )
                strategy.name = name
                return strategy

            factories[name] = grid_factory
            families[name] = "breakout_grid"
    for name, factory in opportunity_factories().items():
        factories[name] = factory
        if "drop" in name:
            families[name] = "shock_rebound"
        elif "momentum" in name:
            families[name] = "momentum"
        else:
            families[name] = "breakout_retest"
    return factories, families


def build_report(
    candles: Sequence[Candle],
    *,
    generated_at: datetime | None = None,
    config: OpportunityResearchConfig | None = None,
) -> dict[str, Any]:
    return _build_report(
        candles,
        generated_at=generated_at,
        config=config,
        evaluate_holdout=False,
        holdout_reservation=None,
    )


def open_holdout_once(
    candles: Sequence[Candle],
    ledger_path: Path,
    *,
    generated_at: datetime | None = None,
    config: OpportunityResearchConfig | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    sealed = build_report(candles, generated_at=generated_at, config=config)
    finalists = list(sealed["development"]["finalists"][:1])
    if not finalists:
        return sealed, None
    reservation = {
        "schema_version": 1,
        "state": "opening",
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": sealed["dataset"]["sha256"],
        "holdout_sha256": sealed["sealed_holdout"]["sha256"],
        "candidate_manifest_sha256": sealed["candidate_manifest"]["sha256"],
        "protocol_sha256": _json_sha256(sealed["protocol"]),
        "finalists": finalists,
    }
    verified = _create_exclusive_reservation(ledger_path, reservation)
    report = _build_report(
        candles,
        generated_at=datetime.fromisoformat(str(sealed["generated_at"])),
        config=config,
        evaluate_holdout=True,
        holdout_reservation=verified,
    )
    return report, verified


def _build_report(
    candles: Sequence[Candle],
    *,
    generated_at: datetime | None,
    config: OpportunityResearchConfig | None,
    evaluate_holdout: bool,
    holdout_reservation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    selected = config or OpportunityResearchConfig()
    sample, data_quality = _sample(candles, selected)
    development = sample[: selected.development_count]
    factories, families = candidate_registry()
    rows: list[dict[str, Any]] = []
    for name in sorted(factories):
        strategy = factories[name]()
        if getattr(strategy, "name", name) != name:
            raise ValueError(f"candidate factory name mismatch: {name}")
        signals = tuple(Signal(value) for value in strategy.generate(development))
        _validate_signals(signals, development)
        base = _evaluate(
            development,
            signals,
            selected,
            stress=False,
            bootstrap_seed=name,
        )
        stress = _evaluate(
            development,
            signals,
            selected,
            stress=True,
            bootstrap_seed=f"stress:{name}",
        )
        classification = classify_candidate(base, stress, is_cash=name == "cash")
        rows.append(
            {
                "name": name,
                "family": families[name],
                "base": base,
                "double_cost_stress": stress,
                "classification": classification,
            }
        )

    priority = {
        "FINALIST": 6,
        "SPARSE_INCUBATOR": 5,
        "FRAGILE_INCUBATOR": 4,
        "REPAIR_QUEUE": 3,
        "DORMANT": 2,
        "REJECTED": 1,
        "CONTROL": 0,
    }
    ranked = sorted(
        (row for row in rows if row["name"] != "cash"),
        key=lambda row: (
            priority[row["classification"]["status"]],
            row["double_cost_stress"]["total_return"],
            row["base"]["total_return"],
            row["name"],
        ),
        reverse=True,
    )
    finalists: list[str] = []
    finalist_families: set[str] = set()
    for row in ranked:
        if row["classification"]["status"] != "FINALIST":
            continue
        if row["family"] in finalist_families:
            continue
        finalists.append(row["name"])
        finalist_families.add(row["family"])
        if len(finalists) == selected.maximum_holdout_candidates:
            break

    opened_names = finalists[:1] if evaluate_holdout else []
    identity = dataset_manifest(sample)
    manifest = _candidate_manifest(factories, families)
    if evaluate_holdout:
        assert holdout_reservation is not None
        _validate_holdout_reservation(
            holdout_reservation,
            dataset_sha256=identity.sha256,
            holdout_sha256=dataset_manifest(
                sample[selected.development_count :]
            ).sha256,
            candidate_manifest_sha256=str(manifest["sha256"]),
            protocol_sha256=_json_sha256(
                protocol_manifest(selected, holdout_opened=False)
            ),
            finalists=opened_names,
        )
    holdout_results: list[dict[str, Any]] = []
    development_signals = {
        row["name"]: tuple(
            Signal(value) for value in factories[row["name"]]().generate(development)
        )
        for row in rows
        if row["name"] in opened_names
    }
    for name in opened_names:
        full_signals = tuple(
            Signal(value) for value in factories[name]().generate(sample)
        )
        _validate_signals(full_signals, sample)
        if full_signals[: selected.development_count] != development_signals[name]:
            raise ValueError(f"holdout candidate is not prefix-stable: {name}")
        base = _evaluate_holdout(
            sample,
            full_signals,
            selected,
            stress=False,
            bootstrap_seed=f"holdout:{name}",
        )
        stress = _evaluate_holdout(
            sample,
            full_signals,
            selected,
            stress=True,
            bootstrap_seed=f"holdout:stress:{name}",
        )
        holdout_results.append(
            {
                "name": name,
                "base": base,
                "double_cost_stress": stress,
                "gate_evaluation": evaluate_holdout_gate(base, stress),
            }
        )

    passed_holdout = [
        row for row in holdout_results if row["gate_evaluation"]["status"] == "PASSED"
    ]

    return {
        "schema_version": 1,
        "generated_at": (generated_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "mission": "profit-first opportunity search without automatic promotion",
        "dataset": {
            "market": identity.market,
            "candle_count": identity.candle_count,
            "start_at": identity.start_at.isoformat() if identity.start_at else None,
            "end_at": identity.end_at.isoformat() if identity.end_at else None,
            "sha256": identity.sha256,
            "gap_count": _gap_count(sample),
            "source": "Bithumb public completed 30-minute OHLCV",
            "data_quality": data_quality,
        },
        "candidate_manifest": manifest,
        "protocol": protocol_manifest(selected, holdout_opened=bool(opened_names)),
        "development": {
            "candidate_count": len(rows),
            "candidates": rows,
            "ranking": [row["name"] for row in ranked],
            "status_counts": {
                status: sum(
                    row["classification"]["status"] == status for row in rows
                )
                for status in priority
            },
            "finalists": finalists,
        },
        "sealed_holdout": {
            "count": selected.sealed_holdout_count,
            "start_at": sample[selected.development_count].timestamp.isoformat(),
            "end_at": sample[-1].timestamp.isoformat(),
            "sha256": dataset_manifest(sample[selected.development_count :]).sha256,
            "opened": bool(opened_names),
            "evaluated_candidates": opened_names,
            "results": holdout_results,
        },
        "selection": {
            "research_candidate": passed_holdout[0]["name"] if passed_holdout else "cash",
            "historical_finalist_found": bool(finalists),
            "development_finalist": finalists[0] if finalists else "cash",
            "holdout_status": (
                holdout_results[0]["gate_evaluation"]["status"]
                if holdout_results
                else "SEALED"
            ),
            "automatic_promotion": "forbidden",
            "can_promote": False,
            "paper_or_live_strategy_changed": False,
            "live_new_entries_changed": False,
            "next_action": (
                "create one-time holdout ledger before evaluating finalist"
                if finalists and not evaluate_holdout
                else "collect prospective paper evidence"
                if passed_holdout
                else "extend prospective evidence without reusing the holdout"
                if holdout_results
                else "retain incubators for prospective paper evidence and new hypotheses"
            ),
        },
        "deferred_hypotheses": [
            {
                "name": "regime_shrunk_selector_v1",
                "reason": "requires nested inner-OOS expert selection and is not honestly testable as a fixed candle-only factory",
            },
            {
                "name": "crossfit_meta_union_v1",
                "reason": "requires purged cross-fitting, triple-barrier labels, and at least 20 inner-OOS events",
            },
            {
                "name": "orderflow_absorption_rebound",
                "reason": "requires 30-60 days of prospective trade and order-book observations",
            },
        ],
        "limitations": [
            "Historical profitability is not a promise of future profit.",
            "The 96,000-candle development period is reused hypothesis-development data.",
            "Trade-block bootstrap is diagnostic and does not remove market-regime dependence.",
            "OHLCV cannot reproduce queue position, spread spikes, or partial fills.",
            (
                "The sealed 4,000-candle holdout was opened once for one finalist."
                if opened_names
                else "The sealed 4,000-candle holdout remains unopened."
            ),
            "Bithumb spot execution remains LONG/FLAT; no synthetic short is claimed.",
        ],
    }


def _json_sha256(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _reservation_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")


def _create_exclusive_reservation(
    path: Path, reservation: Mapping[str, Any]
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _reservation_bytes(reservation)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise HoldoutLedgerExistsError(
            f"research run refused because holdout ledger exists at {path}"
        ) from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    if path.read_bytes() != payload:
        raise ValueError("holdout reservation bytes differ after exclusive creation")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("holdout reservation permissions must be 0600")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("holdout reservation must be a JSON object")
    return loaded


def _validate_holdout_reservation(
    reservation: Mapping[str, Any],
    *,
    dataset_sha256: str,
    holdout_sha256: str,
    candidate_manifest_sha256: str,
    protocol_sha256: str,
    finalists: Sequence[str],
) -> None:
    expected_keys = {
        "schema_version",
        "state",
        "created_at",
        "dataset_sha256",
        "holdout_sha256",
        "candidate_manifest_sha256",
        "protocol_sha256",
        "finalists",
    }
    if set(reservation) != expected_keys:
        raise ValueError("holdout reservation fields differ from protocol")
    expected = {
        "schema_version": 1,
        "state": "opening",
        "dataset_sha256": dataset_sha256,
        "holdout_sha256": holdout_sha256,
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "protocol_sha256": protocol_sha256,
        "finalists": list(finalists),
    }
    for key, value in expected.items():
        if reservation.get(key) != value:
            raise ValueError(f"holdout reservation commitment differs: {key}")
    created_at = datetime.fromisoformat(str(reservation["created_at"]))
    if created_at.tzinfo is None:
        raise ValueError("holdout reservation timestamp must be timezone-aware")


def evaluate_holdout_gate(
    base: Mapping[str, Any], stress: Mapping[str, Any]
) -> dict[str, Any]:
    trades = int(base["closed_trade_count"])
    checks = {
        "closed_trades_gte_5": trades >= 5,
        "base_return_gt_cash": base["total_return"] > 0,
        "double_cost_return_gt_cash": stress["total_return"] > 0,
        "profit_factor_gte_1_05": _profit_factor(base) >= 1.05,
        "maximum_drawdown_lte_12pct": base["maximum_drawdown"] <= 0.12,
        "bootstrap_probability_gte_80pct": (
            base["bootstrap_probability_net_positive"] >= 0.80
        ),
        "forced_final_liquidation_count_eq_0": (
            base["forced_final_liquidation_count"] == 0
            and stress["forced_final_liquidation_count"] == 0
        ),
    }
    if all(checks.values()):
        status = "PASSED"
    elif trades < 5 and base["total_return"] > -0.10 and stress["total_return"] > -0.15:
        status = "INCONCLUSIVE"
    else:
        status = "FAILED"
    return {"status": status, "passed": status == "PASSED", "checks": checks}


def classify_candidate(
    base: Mapping[str, Any], stress: Mapping[str, Any], *, is_cash: bool = False
) -> dict[str, Any]:
    if is_cash:
        return {"status": "CONTROL", "reasons": ["cash baseline"]}
    trades = int(base["closed_trade_count"])
    profit_factor = _profit_factor(base)
    active_folds = int(base["active_fold_count"])
    positive_fraction = (
        base["positive_active_fold_count"] / active_folds if active_folds else 0.0
    )
    catastrophic = (
        base["total_return"] <= -0.10
        or stress["total_return"] <= -0.15
        or base["maximum_drawdown"] > 0.30
        or (trades >= 20 and profit_factor < 0.70)
    )
    finalist = (
        trades >= 20
        and base["total_return"] > 0
        and stress["total_return"] > 0
        and profit_factor >= 1.10
        and _profit_factor(stress) >= 1.00
        and base["maximum_drawdown"] <= 0.15
        and active_folds >= 4
        and positive_fraction >= 0.60
        and base["maximum_single_win_contribution"] <= 0.50
        and base["bootstrap_probability_net_positive"] >= 0.80
    )
    sparse = (
        3 <= trades <= 9
        and base["total_return"] > 0
        and stress["total_return"] > 0
        and profit_factor >= 1.05
        and base["maximum_drawdown"] <= 0.15
        and active_folds >= 2
    )
    fragile = (
        trades >= 10
        and base["total_return"] >= -0.02
        and stress["total_return"] >= -0.08
        and profit_factor >= 0.90
        and base["maximum_drawdown"] <= 0.20
        and active_folds >= 3
    )
    repair = (
        trades >= 5
        and base["maximum_drawdown"] <= 0.20
        and active_folds >= 2
        and (base["total_return"] > 0 or profit_factor >= 0.90)
        and stress["total_return"] >= -0.08
    )
    if catastrophic:
        status = "REJECTED"
    elif finalist:
        status = "FINALIST"
    elif sparse:
        status = "SPARSE_INCUBATOR"
    elif fragile:
        status = "FRAGILE_INCUBATOR"
    elif repair:
        status = "REPAIR_QUEUE"
    elif trades < 3:
        status = "DORMANT"
    else:
        status = "REJECTED"
    return {
        "status": status,
        "reasons": _classification_reasons(base, stress, profit_factor, positive_fraction),
    }


def protocol_manifest(
    config: OpportunityResearchConfig, *, holdout_opened: bool = False
) -> dict[str, Any]:
    return {
        "protocol_name": "profit_search_v1",
        "sample": asdict(config),
        "market_type": "Bithumb KRW spot LONG/FLAT",
        "signal_observed_at": "completed_30m_close",
        "execution_eligible_at": "next_30m_open",
        "gap_policy": "liquidate and suppress stale state at first post-gap open",
        "costs": {
            "base": {"fee_rate_per_fill": 0.0025, "slippage_bps_per_fill": 5.0},
            "double_cost_stress": {"fee_rate_per_fill": 0.005, "slippage_bps_per_fill": 10.0},
        },
        "folds": [
            {
                "train": [0, config.initial_train_count + fold * config.development_test_count],
                "test": [
                    config.initial_train_count + fold * config.development_test_count,
                    config.initial_train_count + (fold + 1) * config.development_test_count,
                ],
            }
            for fold in range(config.development_fold_count)
        ],
        "holdout": {
            "opened": holdout_opened,
            "requires_explicit_one_time_ledger": True,
            "maximum_candidates": config.maximum_holdout_candidates,
            "family_deduplication": True,
        },
        "promotion": "forbidden; historical finalist can only become a paper candidate",
        "multiple_testing": {
            "prior_non_cash_evaluations": 36,
            "current_non_cash_evaluations": len(candidate_registry()[0]) - 1,
            "repeated_candidates_count_as_new_trials": True,
            "selection_control": "one finalist per correlated family, maximum three",
        },
    }


def _evaluate(
    candles: Sequence[Candle],
    signals: Sequence[Signal],
    config: OpportunityResearchConfig,
    *,
    stress: bool,
    bootstrap_seed: str,
) -> dict[str, Any]:
    source = candles[config.initial_train_count - 1 : config.development_count]
    normalized = _flat_start_signals(
        signals,
        start=config.initial_train_count,
        end=config.development_count,
    )
    backtester = Backtester(
        normalized_settings(stress=stress),
        allow_short=False,
        expected_interval=SOURCE_DELTA,
    )
    result = backtester.run(source, normalized)
    metrics = _metrics(result, bootstrap_seed=bootstrap_seed)
    folds: list[dict[str, Any]] = []
    for fold in range(config.development_fold_count):
        start = fold * config.development_test_count
        sliced = backtester.slice_result(
            result,
            source,
            start=start,
            end=start + config.development_test_count,
        )
        fold_metrics = _metrics(sliced, bootstrap_seed=f"{bootstrap_seed}:{fold}")
        folds.append(
            {
                "fold": fold + 1,
                "total_return": fold_metrics["total_return"],
                "maximum_drawdown": fold_metrics["maximum_drawdown"],
                "closed_trade_count": fold_metrics["closed_trade_count"],
                "win_rate": fold_metrics["win_rate"],
            }
        )
    metrics["folds"] = folds
    metrics["active_fold_count"] = sum(fold["closed_trade_count"] > 0 for fold in folds)
    metrics["positive_active_fold_count"] = sum(
        fold["closed_trade_count"] > 0 and fold["total_return"] > 0 for fold in folds
    )
    return metrics


def _evaluate_holdout(
    candles: Sequence[Candle],
    signals: Sequence[Signal],
    config: OpportunityResearchConfig,
    *,
    stress: bool,
    bootstrap_seed: str,
) -> dict[str, Any]:
    source = candles[config.development_count - 1 : config.historical_count]
    normalized = _flat_start_signals(
        signals,
        start=config.development_count,
        end=config.historical_count,
    )
    result = Backtester(
        normalized_settings(stress=stress),
        allow_short=False,
        expected_interval=SOURCE_DELTA,
    ).run(source, normalized)
    return _metrics(result, bootstrap_seed=bootstrap_seed)


def _metrics(result: BacktestResult, *, bootstrap_seed: str) -> dict[str, Any]:
    trades = tuple(trade for trade in result.trades if not trade.is_final_liquidation)
    wins = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    losses = [-trade.net_pnl for trade in trades if trade.net_pnl < 0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    maximum_win_contribution = max(wins, default=0.0) / gross_profit if gross_profit else 0.0
    return {
        "initial_equity_krw": result.initial_equity,
        "final_equity_krw": result.final_equity,
        "total_return": result.total_return,
        "maximum_drawdown": result.max_drawdown,
        "sharpe": result.sharpe,
        "exposure": result.exposure,
        "closed_trade_count": len(trades),
        "forced_final_liquidation_count": sum(trade.is_final_liquidation for trade in result.trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "profit_factor_is_infinite": bool(gross_profit and not gross_loss),
        "net_expectancy_per_trade_krw": sum(trade.net_pnl for trade in trades) / len(trades) if trades else 0.0,
        "maximum_single_win_contribution": maximum_win_contribution,
        "bootstrap_probability_net_positive": _trade_block_bootstrap_probability(
            trades, seed=bootstrap_seed
        ),
    }


def _trade_block_bootstrap_probability(
    trades: Sequence[Trade], *, seed: str, iterations: int = 2_000, block_size: int = 5
) -> float:
    if not trades:
        return 0.0
    pnl = [trade.net_pnl for trade in trades]
    digest = sha256(seed.encode("utf-8")).digest()
    generator = random.Random(int.from_bytes(digest[:8], "big"))
    positive = 0
    for _ in range(iterations):
        sample: list[float] = []
        while len(sample) < len(pnl):
            start = generator.randrange(len(pnl))
            sample.extend(pnl[(start + offset) % len(pnl)] for offset in range(block_size))
        positive += sum(sample[: len(pnl)]) > 0
    return positive / iterations


def _flat_start_signals(
    signals: Sequence[Signal], *, start: int, end: int
) -> tuple[Signal, ...]:
    source = signals[start - 1 : end]
    armed = source[0] is Signal.FLAT
    normalized: list[Signal] = []
    for signal in source:
        if not armed:
            normalized.append(Signal.FLAT)
            if signal is Signal.FLAT:
                armed = True
        else:
            normalized.append(signal)
    return tuple(normalized)


def _candidate_manifest(
    factories: Mapping[str, CandidateFactory], families: Mapping[str, str]
) -> dict[str, Any]:
    rows = []
    for name in sorted(factories):
        strategy = factories[name]()
        module_path = Path(__import__(strategy.__class__.__module__, fromlist=["_"]).__file__ or "")
        rows.append(
            {
                "name": name,
                "family": families[name],
                "class": f"{strategy.__class__.__module__}.{strategy.__class__.__qualname__}",
                "module_sha256": sha256(module_path.read_bytes()).hexdigest(),
                "configuration": _strategy_configuration(strategy),
            }
        )
    payload = {
        "candidate_count": len(rows),
        "candidates": rows,
        "registry_source_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    payload["sha256"] = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def _strategy_configuration(strategy: Any) -> dict[str, Any]:
    if isinstance(strategy, CompletedIntervalStrategy):
        inner = strategy.inner
        parameters = getattr(inner, "parameters", None)
        return {
            "source_minutes": strategy.source_minutes,
            "target_minutes": strategy.target_minutes,
            "inner_class": f"{inner.__class__.__module__}.{inner.__class__.__qualname__}",
            "inner_parameters": asdict(cast(Any, parameters)) if is_dataclass(parameters) else None,
        }
    if is_dataclass(strategy):
        return asdict(cast(Any, strategy))
    parameters = getattr(strategy, "parameters", None)
    return {
        "parameters": asdict(cast(Any, parameters)) if is_dataclass(parameters) else None
    }


def _classification_reasons(
    base: Mapping[str, Any],
    stress: Mapping[str, Any],
    profit_factor: float,
    positive_fraction: float,
) -> list[str]:
    return [
        f"closed_trades={base['closed_trade_count']}",
        f"base_return={base['total_return']:.6f}",
        f"stress_return={stress['total_return']:.6f}",
        f"profit_factor={profit_factor:.6f}",
        f"maximum_drawdown={base['maximum_drawdown']:.6f}",
        f"positive_active_fold_fraction={positive_fraction:.6f}",
        f"bootstrap_p_net_positive={base['bootstrap_probability_net_positive']:.6f}",
    ]


def _profit_factor(metrics: Mapping[str, Any]) -> float:
    if metrics["profit_factor_is_infinite"]:
        return float("inf")
    value = metrics["profit_factor"]
    return float(value) if value is not None else 0.0


def _sample(
    candles: Sequence[Candle], config: OpportunityResearchConfig
) -> tuple[tuple[Candle, ...], dict[str, Any]]:
    raw = tuple(candles)
    if any(
        raw[index].timestamp <= raw[index - 1].timestamp
        for index in range(1, len(raw))
    ):
        raise ValueError("candles must be strictly chronological")
    rejected = tuple(
        candle
        for candle in raw
        if candle.timestamp.minute % 30
        or candle.timestamp.second
        or candle.timestamp.microsecond
    )
    aligned = tuple(candle for candle in raw if candle not in rejected)
    if len(aligned) < config.historical_count:
        raise ValueError(
            f"opportunity research requires {config.historical_count} aligned candles; "
            f"found {len(aligned)}"
        )
    sample = aligned[-config.historical_count :]
    if {candle.market for candle in sample} != {"KRW-BTC"}:
        raise ValueError("opportunity research requires KRW-BTC")
    return sample, {
        "raw_candle_count": len(raw),
        "aligned_candle_count": len(aligned),
        "selected_candle_count": len(sample),
        "rejected_non_aligned_count": len(rejected),
        "rejected_timestamps": [candle.timestamp.isoformat() for candle in rejected],
        "selection_policy": (
            "drop non-30m-aligned exchange anomaly candles, then take latest "
            f"{config.historical_count}"
        ),
    }


def _validate_signals(signals: Sequence[Signal], candles: Sequence[Candle]) -> None:
    if len(signals) != len(candles):
        raise ValueError("candidate signal count differs from candle count")
    if any(signal not in {Signal.FLAT, Signal.LONG} for signal in signals):
        raise ValueError("opportunity research is LONG/FLAT only")


def _gap_count(candles: Sequence[Candle]) -> int:
    return sum(
        candles[index].timestamp - candles[index - 1].timestamp != SOURCE_DELTA
        for index in range(1, len(candles))
    )
