from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bithumb_coin_trader.research_infra import cli
from bithumb_coin_trader.research_infra.build import CanonicalBuildError, build_canonical_dataset
from bithumb_coin_trader.research_infra.registry import DatasetRegistration, DatasetRole, DatasetRegistry


def _registration(
    *,
    role: DatasetRole = DatasetRole.DEVELOPMENT_EXPLORATORY,
    allowed_for_exploration: bool = True,
    allowed_for_final_holdout: bool = False,
) -> DatasetRegistration:
    return DatasetRegistration(
        dataset_id="local-dev",
        dataset_role=role,
        description="synthetic test input",
        source_type="jsonl_raw",
        source_roots=(),
        time_range_start=None,
        time_range_end=None,
        exchange_universe=("bithumb",),
        feed_universe=("trade",),
        raw_schema_version="test",
        manifest_schema_version="1",
        known_integrity_status="UNKNOWN",
        known_data_quality_issues=(),
        allowed_for_exploration=allowed_for_exploration,
        allowed_for_candidate_selection=False,
        allowed_for_final_holdout=allowed_for_final_holdout,
        immutable_source=True,
    )


def _write_source(root: Path) -> Path:
    source = root / "2026-09-01" / "bithumb" / "trade" / "trades.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps({
            "exchange": "bithumb",
            "stream": "trade",
            "market": "KRW-BTC",
            "exchange_ts": "2026-09-01T00:00:00.100000+00:00",
            "local_recv_ts": "2026-09-01T00:00:00.200000+00:00",
            "local_write_ts": "2026-09-01T00:00:00.201000+00:00",
            "payload": {
                "type": "trade",
                "code": "KRW-BTC",
                "trade_price": 100_000_000,
                "trade_volume": 0.01,
                "ask_bid": "BID",
                "sequential_id": 1,
            },
        }) + "\n",
        encoding="utf-8",
    )
    return source


def test_build_persists_source_bound_canonical_output_and_dq_pending(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    source = _write_source(raw_root)
    output = tmp_path / "artifacts" / "local-dev"

    manifest = build_canonical_dataset(
        _registration(),
        data_root=raw_root,
        output_dir=output,
        git_commit="a" * 40,
    )

    events_path = output / "events.jsonl"
    event = json.loads(events_path.read_text(encoding="utf-8").splitlines()[0])
    assert manifest["event_count"] == 1
    assert manifest["build_status"] == "BUILT_DQ_NOT_RUN"
    assert manifest["dq_status"] == "NOT_RUN"
    assert manifest["holdout_evaluated"] is False
    assert event["source_file"] == "2026-09-01/bithumb/trade/trades.jsonl"
    assert manifest["source_files"][0]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["events_sha256"] == hashlib.sha256(events_path.read_bytes()).hexdigest()


def test_build_refuses_holdout_before_reading_source(tmp_path: Path) -> None:
    missing_raw = tmp_path / "must-not-be-read"
    with pytest.raises(CanonicalBuildError, match="holdout"):
        build_canonical_dataset(
            _registration(
                role=DatasetRole.FROZEN_HOLDOUT,
                allowed_for_final_holdout=True,
            ),
            data_root=missing_raw,
            output_dir=tmp_path / "output",
            git_commit="a" * 40,
        )
    assert not missing_raw.exists()


def test_build_refuses_existing_output_and_raw_tree_output(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    _write_source(raw_root)
    existing = tmp_path / "existing"
    existing.mkdir()

    with pytest.raises(CanonicalBuildError, match="already exists"):
        build_canonical_dataset(
            _registration(), data_root=raw_root, output_dir=existing, git_commit="a" * 40
        )
    with pytest.raises(CanonicalBuildError, match="outside the raw data root"):
        build_canonical_dataset(
            _registration(), data_root=raw_root, output_dir=raw_root / "output", git_commit="a" * 40
        )


def test_build_refuses_unexplorable_registry_entry(tmp_path: Path, monkeypatch, capsys) -> None:
    raw_root = tmp_path / "raw"
    _write_source(raw_root)
    registry_path = tmp_path / "registry.json"
    registry = DatasetRegistry()
    registry.register(_registration(allowed_for_exploration=False))
    registry.save(registry_path)
    monkeypatch.setattr(cli, "_get_registry_path", lambda: registry_path)

    result = cli.main([
        "build",
        "--dataset", "local-dev",
        "--data-root", str(raw_root),
        "--output-dir", str(tmp_path / "output"),
    ])

    assert result == 2
    assert "not allowed for exploration" in capsys.readouterr().err
