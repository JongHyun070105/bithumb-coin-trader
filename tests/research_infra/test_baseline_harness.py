from __future__ import annotations

import math
import random

import pytest

from bithumb_coin_trader.research_infra.baseline_harness import (
    Bar, BuyAndHold, CostModel, FlatStrategy, HoldoutAccessError, HoldoutVault, SmaCross,
    assert_no_lookahead, candidate_fingerprint, chronological_split, compute_metrics,
    cost_sensitivity, run_backtest, validate_bars,
)


def make_bars(n=300, seed=7, drift=0.0004):
    rng = random.Random(seed)
    price, out = 100.0, []
    for i in range(n):
        price *= 1.0 + rng.gauss(drift, 0.01)
        out.append(Bar(1_790_000_000_000 + i * 60_000, price))
    return out


BARS = make_bars()


class OracleStrategy:
    """Deliberately leaky: reads the true next close from the full series handed at construction."""
    def __init__(self, series): self.series = series
    def target_position(self, history):
        t = len(history) - 1
        return 1.0 if self.series[t + 1].close > self.series[t].close else -1.0


class FullSampleNormStrategy:
    """Deliberately leaky: z-scores against full-sample statistics."""
    def __init__(self, series):
        c = [b.close for b in series]
        self.mu = sum(c) / len(c)
        self.sd = math.sqrt(sum((x - self.mu) ** 2 for x in c) / len(c))
    def target_position(self, history):
        return 1.0 if (history[-1].close - self.mu) / self.sd > 0 else 0.0


# ---- leakage

def test_clean_strategies_pass_lookahead_guard() -> None:
    for factory in (lambda s: SmaCross(5, 20), lambda s: BuyAndHold(), lambda s: FlatStrategy()):
        assert_no_lookahead(factory, BARS, cut_points=[25, 100, 200])


def test_oracle_peeking_strategy_is_caught() -> None:
    with pytest.raises(AssertionError, match="lookahead"):
        assert_no_lookahead(lambda s: OracleStrategy(s), BARS, cut_points=[50, 150])


def test_full_sample_normalisation_leak_is_caught() -> None:
    with pytest.raises(AssertionError, match="lookahead"):
        assert_no_lookahead(lambda s: FullSampleNormStrategy(s), BARS, cut_points=[100])


def test_history_passed_to_strategy_never_contains_the_future() -> None:
    seen = []
    class Spy:
        def target_position(self, history):
            seen.append((len(history), history[-1].ts_ms))
            return 0.0
    run_backtest(BARS, Spy(), CostModel())
    assert [n for n, _ in seen] == list(range(1, len(BARS)))
    assert all(ts == BARS[n - 1].ts_ms for n, ts in seen)


def test_position_at_t_earns_only_the_t_to_t_plus_1_return() -> None:
    class AlwaysLongAfterFirst:
        def target_position(self, history): return 1.0
    res = run_backtest(BARS, AlwaysLongAfterFirst(), CostModel(0, 0))
    assert res.gross_returns[0] == pytest.approx(BARS[1].close / BARS[0].close - 1)
    assert len(res.net_returns) == len(BARS) - 1


# ---- splits and holdout

def test_split_ranges_are_ordered_disjoint_and_embargoed() -> None:
    s = chronological_split(1000, train_frac=0.6, validation_frac=0.2, embargo_bars=10)
    assert s.train[1] + 10 == s.validation[0] and s.validation[1] + 10 == s.holdout[0]
    assert s.train[0] == 0 and s.holdout[1] <= 1000
    assert s.train[1] <= s.validation[0] <= s.validation[1] <= s.holdout[0]


@pytest.mark.parametrize("kw", [
    dict(train_frac=0.9, validation_frac=0.2, embargo_bars=1), dict(train_frac=0.5, validation_frac=0.2, embargo_bars=-1),
    dict(train_frac=0, validation_frac=0.2, embargo_bars=1),
])
def test_invalid_split_parameters_rejected(kw) -> None:
    with pytest.raises(ValueError):
        chronological_split(100, **kw)


def test_short_series_rejected() -> None:
    with pytest.raises(ValueError):
        chronological_split(12, train_frac=0.5, validation_frac=0.25, embargo_bars=5)


def test_holdout_vault_is_single_use_and_fingerprint_bound() -> None:
    spec = {"name": "sma", "fast": 5, "slow": 20, "cost_bps": 6}
    fp = candidate_fingerprint(spec)
    vault = HoldoutVault(BARS[-50:], fp)
    with pytest.raises(HoldoutAccessError):
        vault.open(candidate_fingerprint({**spec, "fast": 6}))
    assert len(vault.open(fp)) == 50
    with pytest.raises(HoldoutAccessError):
        vault.open(fp)
    assert vault.access_log == ["DENIED_FINGERPRINT", "OPENED", "DENIED_REUSE"]
    assert "sealed" in repr(vault)


def test_fingerprint_is_order_independent_and_value_sensitive() -> None:
    assert candidate_fingerprint({"a": 1, "b": 2}) == candidate_fingerprint({"b": 2, "a": 1})
    assert candidate_fingerprint({"a": 1}) != candidate_fingerprint({"a": 2})
    with pytest.raises(ValueError):
        candidate_fingerprint({"a": float("nan")})


# ---- validation of inputs

@pytest.mark.parametrize("bars", [
    [Bar(1, 1.0), Bar(1, 1.0), Bar(2, 1.0)], [Bar(1, 1.0), Bar(2, -1.0), Bar(3, 1.0)],
    [Bar(1, 1.0), Bar(2, float("nan")), Bar(3, 1.0)], [Bar(1, 1.0), Bar(2, 1.0)],
])
def test_bad_bars_rejected(bars) -> None:
    with pytest.raises(ValueError):
        validate_bars(bars)


def test_out_of_range_position_rejected() -> None:
    class Greedy:
        def target_position(self, history): return 2.0
    with pytest.raises(ValueError):
        run_backtest(BARS, Greedy(), CostModel())


# ---- metrics

def test_flat_strategy_metrics_are_zero() -> None:
    m = compute_metrics(run_backtest(BARS, FlatStrategy(), CostModel()), periods_per_year=525_600)
    assert (m.total_return, m.max_drawdown, m.sharpe, m.exposure, m.trade_count, m.turnover_total) == (0, 0, 0, 0, 0, 0)
    assert m.hit_rate is None


def test_buy_and_hold_with_zero_cost_matches_price_return() -> None:
    m = compute_metrics(run_backtest(BARS, BuyAndHold(), CostModel(0, 0)), periods_per_year=525_600)
    assert m.total_return == pytest.approx(BARS[-1].close / BARS[0].close - 1)
    assert m.exposure == 1.0 and m.trade_count == 1 and m.turnover_total == 1.0


def test_known_small_series_metrics() -> None:
    bars = [Bar(i, c) for i, c in enumerate([100, 110, 99, 99, 118.8])]
    m = compute_metrics(run_backtest(bars, BuyAndHold(), CostModel(0, 0)), periods_per_year=4)
    assert m.total_return == pytest.approx(0.188)
    assert m.max_drawdown == pytest.approx(0.1)
    assert m.cagr == pytest.approx(1.188 - 1)  # 4 periods at 4/yr = exactly 1 year
    assert m.hit_rate == pytest.approx(0.5)


def test_costs_are_charged_on_position_change_only() -> None:
    res = run_backtest(BARS, BuyAndHold(), CostModel(10, 0))
    assert res.gross_returns[0] - res.net_returns[0] == pytest.approx(0.001)
    assert res.gross_returns[5] == pytest.approx(res.net_returns[5])


def test_cost_sensitivity_is_monotone_for_a_trading_strategy() -> None:
    out = cost_sensitivity(BARS, lambda: SmaCross(3, 8), CostModel(), periods_per_year=525_600)
    rets = [out[m].total_return for m in sorted(out)]
    assert rets == sorted(rets, reverse=True) and out[0.0].trade_count > 1


def test_wipeout_equity_gives_full_drawdown_and_minus_one_cagr() -> None:
    bars = [Bar(i, c) for i, c in enumerate([100, 200, 200, 200])]
    class Short:
        def target_position(self, history): return -1.0
    m = compute_metrics(run_backtest(bars, Short(), CostModel(0, 0)), periods_per_year=365)
    assert m.max_drawdown == 1.0 and m.cagr == -1.0


def test_backtest_is_deterministic() -> None:
    a = run_backtest(BARS, SmaCross(), CostModel())
    b = run_backtest(BARS, SmaCross(), CostModel())
    assert a == b
