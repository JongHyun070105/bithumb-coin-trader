"""Tests for V3 bounded incremental finalization scale and history independence."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Sequence
from unittest.mock import patch

import pytest

from bithumb_coin_trader.incremental_finalizer import (
    ArtifactBinding,
    FinalizationIdentity,
    FinalizationProgressStore,
    IncrementalManifestFinalizer,
)
from bithumb_coin_trader.microstructure_storage import (
    PartitionManifest,
    RawMicrostructureStorage,
)


@dataclass
class OpenCounter:
    paths: set[Path] = field(default_factory=set)
    count: int = 0


@contextmanager
def count_raw_opens() -> Iterator[OpenCounter]:
    counter = OpenCounter()
    orig_path_open = Path.open

    def _is_raw(p: Path | str | int) -> bool:
        if isinstance(p, int):
            return False
        name = Path(p).name
        return name.endswith(".jsonl") or name.endswith(".jsonl.gz") or name.endswith(".jsonl.zst")

    def custom_path_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if _is_raw(self):
            counter.paths.add(self.resolve())
            counter.count += 1
        return orig_path_open(self, *args, **kwargs)

    with patch.object(Path, "open", custom_path_open):
        yield counter


@dataclass
class ProgressHistoryFixture:
    store: FinalizationProgressStore
    storage: RawMicrostructureStorage
    finalizer: IncrementalManifestFinalizer
    dirty_tail_paths: list[Path]
    historical_paths: list[Path]
    receipt_root: Path


def build_progress_history(
    root: Path,
    terminal_cohorts: int = 1,
    dirty_tail: int = 2,
    *,
    exchange: str = "bithumb",
    stream: str = "trade",
    market: str = "KRW-BTC",
    epoch: str = "epoch-001",
    run_id: str = "run-001",
    env_id: str = "aws-v3",
) -> ProgressHistoryFixture:
    raw_root = root / "raw"
    manifest_root = root / "manifests"
    receipt_root = root / "receipts"
    progress_root = root / "progress"

    raw_root.mkdir(parents=True, exist_ok=True)
    manifest_root.mkdir(parents=True, exist_ok=True)
    receipt_root.mkdir(parents=True, exist_ok=True)
    progress_root.mkdir(parents=True, exist_ok=True)

    storage = RawMicrostructureStorage(
        base_dir=raw_root,
        manifest_dir=manifest_root,
    )
    store = FinalizationProgressStore(progress_root)

    clean_market = market.replace("/", "-").replace(":", "-").lower()

    historical_paths: list[Path] = []
    # 1. Create completed historical cohorts
    for i in range(terminal_cohorts):
        cohort_str = f"2026-09-14_{i:02d}"
        raw_rel = f"2026-09-14/{exchange}/{stream}/{exchange}_{stream}_{clean_market}_2026-09-14_{i:02d}.jsonl"
        raw_file = raw_root / raw_rel
        raw_file.parent.mkdir(parents=True, exist_ok=True)

        iso_ts = f"2026-09-14T{i % 24:02d}:30:00+00:00"
        record = {
            "exchange": exchange,
            "stream": stream,
            "market": market,
            "exchange_ts": iso_ts,
            "local_recv_ts": iso_ts,
            "local_write_ts": iso_ts,
            "payload": {
                "price": "100000000",
                "size": "0.01",
                "sequential_id": i + 1,
            },
        }
        raw_bytes = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        raw_file.write_bytes(raw_bytes)
        historical_paths.append(raw_file.resolve())

        raw_sha = hashlib.sha256(raw_bytes).hexdigest()
        raw_size = len(raw_bytes)

        # Write manifest file
        manifest_file = manifest_root / f"manifest_{raw_file.stem}.json"
        manifest_dict = {
            "partition_path": raw_rel,
            "sha256": raw_sha,
            "bytes": raw_size,
            "record_count": 1,
        }
        manifest_bytes = json.dumps(manifest_dict, indent=2).encode("utf-8")
        manifest_file.write_bytes(manifest_bytes)
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()

        # Write receipt file
        receipt_file = receipt_root / f"{raw_file.name}.archive-receipt.json"
        receipt_dict = {
            "schema_version": 2,
            "state": "ARCHIVED",
            "environment_id": env_id,
            "run_id": run_id,
            "collector_epoch": epoch,
            "partition": raw_rel,
            "cohort": cohort_str,
            "exchange": exchange,
            "stream": stream,
            "market": market,
            "feed_identity": f"{exchange}:{stream}:{market}",
            "raw_size": raw_size,
            "raw_sha256": raw_sha,
            "raw_record_count": 1,
            "manifest_relative_path": str(manifest_file.relative_to(manifest_root.parent)),
            "manifest_file_sha256": manifest_sha,
            "artifact_kind": "RAW_DATA",
        }
        receipt_bytes = json.dumps(receipt_dict, indent=2).encode("utf-8")
        receipt_file.write_bytes(receipt_bytes)
        receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()

        identity = FinalizationIdentity(
            environment_id=env_id,
            collector_epoch=epoch,
            collector_run_id=run_id,
            cohort=cohort_str,
            exchange=exchange,
            stream=stream,
            market=market,
            feed_identity=f"{exchange}:{stream}:{market}",
            raw_relative_path=raw_rel,
        )
        store.register_pending(identity)
        binding = ArtifactBinding(
            source_size=raw_size,
            source_sha256=raw_sha,
            source_record_count=1,
            manifest_relative_path=str(manifest_file.relative_to(manifest_root.parent)),
            manifest_file_sha256=manifest_sha,
            receipt_relative_path=str(receipt_file.relative_to(receipt_root)),
            receipt_file_sha256=receipt_sha,
            receipt_state="ARCHIVED",
            artifact_kind="RAW_DATA",
        )
        store.mark_reused(identity.entry_id, binding)

    # 2. Create unfinalized dirty tail cohorts
    dirty_tail_paths: list[Path] = []
    for j in range(dirty_tail):
        cohort_idx = terminal_cohorts + j
        cohort_str = f"2026-09-14_{cohort_idx:02d}"
        raw_rel = f"2026-09-14/{exchange}/{stream}/{exchange}_{stream}_{clean_market}_2026-09-14_{cohort_idx:02d}.jsonl"
        raw_file = raw_root / raw_rel
        raw_file.parent.mkdir(parents=True, exist_ok=True)

        iso_ts = f"2026-09-14T{cohort_idx % 24:02d}:30:00+00:00"
        record = {
            "exchange": exchange,
            "stream": stream,
            "market": market,
            "exchange_ts": iso_ts,
            "local_recv_ts": iso_ts,
            "local_write_ts": iso_ts,
            "payload": {
                "price": "100000000",
                "size": "0.01",
                "sequential_id": cohort_idx + 1,
            },
        }
        raw_bytes = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        raw_file.write_bytes(raw_bytes)
        dirty_tail_paths.append(raw_file.resolve())

        dirty_identity = FinalizationIdentity(
            environment_id=env_id,
            collector_epoch=epoch,
            collector_run_id=run_id,
            cohort=cohort_str,
            exchange=exchange,
            stream=stream,
            market=market,
            feed_identity=f"{exchange}:{stream}:{market}",
            raw_relative_path=raw_rel,
        )
        store.register_pending(dirty_identity)

    finalizer = IncrementalManifestFinalizer(store, storage, receipt_root)
    return ProgressHistoryFixture(
        store=store,
        storage=storage,
        finalizer=finalizer,
        dirty_tail_paths=dirty_tail_paths,
        historical_paths=historical_paths,
        receipt_root=receipt_root,
    )


@dataclass(frozen=True)
class ScaleSample:
    history: int
    dirty_tail: int
    elapsed_seconds: float
    recomputed_count: int
    reused_count: int
    historical_raw_files_opened: int
    historical_raw_bytes_read: int


def run_scale_matrix(
    root: Path,
    histories: Sequence[int],
    dirty_tail: int = 2,
    delay: float = 0.0,
) -> list[ScaleSample]:
    samples: list[ScaleSample] = []
    for h in histories:
        sub_dir = root / f"history_{h}_{time.time_ns()}"
        fixture = build_progress_history(sub_dir, terminal_cohorts=h, dirty_tail=dirty_tail)

        if delay > 0:
            orig_gen = fixture.storage.generate_partition_manifest

            def slow_gen(file_path: Path, *, identity: Any | None = None) -> PartitionManifest:
                time.sleep(delay)
                return orig_gen(file_path, identity=identity)

            fixture.storage.generate_partition_manifest = slow_gen  # type: ignore[method-assign]

        t0 = time.perf_counter()
        summary = fixture.finalizer.finalize_pending()
        t1 = time.perf_counter()

        sample = ScaleSample(
            history=h,
            dirty_tail=dirty_tail,
            elapsed_seconds=t1 - t0,
            recomputed_count=summary.recomputed_count,
            reused_count=summary.reused_count,
            historical_raw_files_opened=summary.historical_raw_files_opened,
            historical_raw_bytes_read=summary.historical_raw_bytes_read,
        )
        samples.append(sample)
    return samples


@pytest.mark.parametrize("history", [1, 10, 30])
def test_only_identical_dirty_tail_is_opened(tmp_path: Path, history: int) -> None:
    fixture = build_progress_history(tmp_path, terminal_cohorts=history, dirty_tail=2)
    with count_raw_opens() as opened:
        summary = fixture.finalizer.finalize_pending()
    assert summary.recomputed_count == 2
    assert summary.historical_raw_files_opened == 0
    assert summary.historical_raw_bytes_read == 0
    assert opened.paths == set(fixture.dirty_tail_paths)


def test_slow_cost_depends_only_on_dirty_tail(tmp_path: Path) -> None:
    samples = run_scale_matrix(tmp_path, (1, 10, 30), dirty_tail=2, delay=0.05)
    assert {sample.recomputed_count for sample in samples} == {2}
    assert max(s.elapsed_seconds for s in samples) - min(s.elapsed_seconds for s in samples) < 0.10
