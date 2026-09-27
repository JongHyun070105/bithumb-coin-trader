from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bithumb_coin_trader.research_infra import cli


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _scenario(name: str, *, fee_bps: float, slip_bps: float, latency_ms: float) -> dict[str, object]:
    return {
        "name": name,
        "maker_fee_bps": fee_bps / 2,
        "taker_fee_bps": fee_bps,
        "slippage_bps": slip_bps,
        "latency_ms": latency_ms,
        "minimum_order_notional": 1,
        "tick_size": 1,
        "lot_size": 1,
        "partial_fill_probability": None,
        "partial_fill_status": "UNSUPPORTED",
    }


def test_research_batch_cli_writes_machine_readable_evidence_and_skips_holdout(tmp_path: Path, monkeypatch, capsys) -> None:
    data = tmp_path / "candles.csv"
    rows = ["market,timestamp,open,high,low,close,volume"]
    prices = [100, 100, 100, 100, 100, 102, 103, 104, 101, 99, 100, 102]
    for index, price in enumerate(prices):
        rows.append(
            f"KRW-BTC,2024-01-{index + 1:02d}T00:00:00+00:00,{price},{price},{price},{price},100"
        )
    data_bytes = ("\n".join(rows) + "\n").encode()
    data.write_bytes(data_bytes)
    dataset_manifest = tmp_path / "dataset.json"
    _write_json(dataset_manifest, {
        "schema_version": 1,
        "dataset_id": "synthetic-development",
        "dataset_role": "DEVELOPMENT_EXPLORATORY",
        "allowed_for_candidate_selection": True,
        "integrity_status": "PASS",
        "provenance_confidence": "PROVEN",
        "data_path": "candles.csv",
        "data_sha256": _sha(data_bytes),
        "candle_count": len(prices),
    })
    hypotheses = tmp_path / "hypotheses.json"
    _write_json(hypotheses, {
        "schema_version": 1,
        "cost_grids": {
            "conservative": [
                _scenario("base", fee_bps=10, slip_bps=5, latency_ms=50),
                _scenario("stress", fee_bps=40, slip_bps=30, latency_ms=250),
            ]
        },
        "experiments": [
            {
                "candidate_family": "baseline_controls",
                "strategy_id": "cash",
                "strategy_config": {},
                "feature_config": {"input": "completed_candles"},
                "parameter_sets": [{}],
                "seed": 11,
            },
            {
                "candidate_family": "baseline_controls",
                "strategy_id": "buy_and_hold",
                "strategy_config": {},
                "feature_config": {"input": "completed_candles"},
                "parameter_sets": [{}],
                "seed": 11,
            },
            {
                "candidate_family": "baseline_controls",
                "strategy_id": "randomized_placebo",
                "strategy_config": {},
                "feature_config": {"input": "completed_candles"},
                "parameter_sets": [{"exposure_probability": 0.5, "target_weight": 0.5}],
                "seed": 11,
            },
            {
                "candidate_family": "builtin_sma_trend_example",
                "strategy_id": "sma_trend",
                "strategy_config": {"mode": "long_flat"},
                "feature_config": {"input": "completed_candles"},
                "parameter_sets": [
                    {"lookback_bars": 2, "target_weight": 0.5, "entry_return_threshold": 0.0},
                    {"lookback_bars": 3, "target_weight": 0.5, "entry_return_threshold": 0.0},
                ],
                "seed": 11,
            },
        ],
    })
    monkeypatch.setattr(cli, "_require_clean_code_revision", lambda: "b" * 40)

    result = cli.main([
        "research-batch",
        "--dataset-manifest", str(dataset_manifest),
        "--hypotheses", str(hypotheses),
        "--cost-grid", "conservative",
        "--walk-forward",
        "--folds", "2",
        "--purge-seconds", "3600",
        "--embargo-seconds", "3600",
        "--output", str(tmp_path / "research-output"),
    ])

    assert result == 0
    output = capsys.readouterr().out
    assert "Status: COMPLETE" in output
    batch_id = next((tmp_path / "research-output" / "batches").iterdir()).name
    batch_dir = tmp_path / "research-output" / "batches" / batch_id
    aggregate = json.loads((batch_dir / "aggregate_report.json").read_text())
    assert aggregate["experiment_count"] == 5
    assert aggregate["completed_count"] == 5
    assert len(aggregate["baseline_comparisons"]) == 24
    assert len(list((batch_dir / "runs").glob("*/attempt-*/manifest.json"))) == 5
    assert len(list((batch_dir / "runs").glob("*/attempt-*/metrics.json"))) == 5

    dataset_data = json.loads(dataset_manifest.read_text())
    dataset_data["dataset_role"] = "FROZEN_HOLDOUT"
    _write_json(dataset_manifest, dataset_data)
    result = cli.main([
        "research-batch",
        "--dataset-manifest", str(dataset_manifest),
        "--hypotheses", str(hypotheses),
        "--walk-forward",
        "--purge-seconds", "3600",
        "--embargo-seconds", "3600",
        "--output", str(tmp_path / "rejected-output"),
    ])
    assert result == 2
    assert "only accepts DEVELOPMENT_EXPLORATORY" in capsys.readouterr().err
    assert not (tmp_path / "rejected-output" / "batches").exists()


def test_research_batch_requires_explicit_walk_forward_flag(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["research-batch", "--help"])
    assert exc.value.code == 0
    assert "--walk-forward" in capsys.readouterr().out
