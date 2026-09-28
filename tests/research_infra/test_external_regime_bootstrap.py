from __future__ import annotations

import pytest

from scripts.analyze_external_regime_bootstrap import main
from bithumb_coin_trader.research_infra.regime_bootstrap import (
    _moving_windows,
    analyze_records,
)


def _row(day: str, liquidity: str, *, symbol: str = "XBTUSD") -> dict[str, str]:
    return {
        "execution_timestamp_utc": f"{day}T12:00:00Z",
        "symbol": symbol,
        "lastliquidityind": liquidity,
        "volatility_regime": "HIGH_VOL",
        "trend_regime": "RANGE",
        "volume_regime": "HIGH_VOLUME",
        "funding_regime": "POSITIVE_FUNDING",
    }


def test_moving_windows_do_not_wrap_at_year_edges() -> None:
    assert _moving_windows([1, 2, 3], 2) == [3, 5]


def test_regime_share_excludes_unknown_liquidity_and_reports_it() -> None:
    result = analyze_records(
        [
            _row("2020-01-01", "AddedLiquidity"),
            _row("2020-01-02", "AddedLiquidity"),
            _row("2020-01-03", "RemovedLiquidity"),
            _row("2020-01-04", "Unknown"),
        ],
        input_sha256="a" * 64,
        reps=200,
        seed=7,
    )

    volatility = next(row for row in result["strata"] if row["dimension"] == "volatility_regime")
    assert volatility["maker_fills"] == 2
    assert volatility["taker_fills"] == 1
    assert volatility["unknown_liquidity_fills"] == 1
    assert volatility["maker_share"] == 2 / 3
    assert volatility["valid_replicates"] == 200
    assert result["symbol_counts"]["XBTUSD"]["rows"] == 4


def test_bootstrap_is_deterministic_for_input_seed_and_stratum() -> None:
    rows = [
        _row("2019-12-30", "AddedLiquidity"),
        _row("2019-12-31", "RemovedLiquidity"),
        _row("2020-01-01", "AddedLiquidity"),
        _row("2020-01-02", "AddedLiquidity"),
        _row("2020-01-03", "RemovedLiquidity"),
    ]
    first = analyze_records(rows, input_sha256="b" * 64, reps=300, seed=19)
    second = analyze_records(rows, input_sha256="b" * 64, reps=300, seed=19)
    assert first == second


def test_cli_refuses_to_overwrite_existing_output(tmp_path, monkeypatch) -> None:
    source = tmp_path / "input.csv.gz"
    source.write_bytes(b"preserve source")
    output = tmp_path / "result.json"
    output.write_text("historical result\n")
    monkeypatch.setattr("sys.argv", ["analysis", "--input", str(source), "--output", str(output)])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 2
    assert output.read_text() == "historical result\n"
