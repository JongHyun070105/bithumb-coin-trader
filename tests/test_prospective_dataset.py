import pytest
import json
from pathlib import Path
from bithumb_coin_trader.canonical_market_data import CanonicalOrderBook
from bithumb_coin_trader.experiment_runner import DatasetRole
from bithumb_coin_trader.prospective_dataset import (
    partition_records_temporally,
    build_and_export_dataset,
)


def test_temporal_partitioning_with_purge_window():
    # 100 records spaced 10 seconds apart (0s to 990s = 990,000ms)
    records = [
        CanonicalOrderBook(
            exchange="bithumb",
            market="KRW-BTC",
            exchange_timestamp_ms=i * 10_000,
            receive_timestamp_ms=i * 10_000,
            bids=((100.0, 1.0),),
            asks=((101.0, 1.0),),
        )
        for i in range(100)
    ]
    # Purge window of 50 seconds (50,000ms = 5 records)
    splits = partition_records_temporally(
        records, train_frac=0.60, val_frac=0.20, purge_window_ms=50_000
    )

    train = splits[DatasetRole.TRAIN]
    val = splits[DatasetRole.VALIDATION]
    holdout = splits[DatasetRole.HOLDOUT]

    assert len(train) == 60
    assert train[-1].receive_timestamp_ms == 590_000

    # Validation must start at or after 590,000 + 50,000 = 640,000ms
    assert val[0].receive_timestamp_ms >= 640_000
    val_end_ts = val[-1].receive_timestamp_ms

    # Holdout must start at or after val_end_ts + 50,000ms
    assert holdout[0].receive_timestamp_ms >= val_end_ts + 50_000


def test_build_and_export_dataset(tmp_path: Path):
    out_dir = tmp_path / "prospective_dataset"
    records = [
        CanonicalOrderBook(
            exchange="bithumb",
            market="KRW-BTC",
            exchange_timestamp_ms=i * 10_000,
            receive_timestamp_ms=i * 10_000,
            bids=((100.0, 1.0),),
            asks=((101.0, 1.0),),
        )
        for i in range(50)
    ]
    manifest = build_and_export_dataset("ds_test_01", out_dir, records, purge_window_ms=20_000)

    assert manifest.dataset_id == "ds_test_01"
    assert (out_dir / "manifest.json").exists()
    assert (out_dir / "train.ndjson.zst").exists()
    assert (out_dir / "validation.ndjson.zst").exists()
    assert (out_dir / "holdout.ndjson.zst").exists()

    manifest_data = json.loads((out_dir / "manifest.json").read_text())
    assert manifest_data["total_records"] == 50
    assert "TRAIN" in manifest_data["partitions"]
