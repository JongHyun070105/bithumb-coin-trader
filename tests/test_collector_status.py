from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_collector_status.py"
SPEC = importlib.util.spec_from_file_location("check_collector_status", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CollectorStatusTests(unittest.TestCase):
    def test_manifest_coverage_excludes_active_hour_and_detects_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_root = MODULE.ROOT
            MODULE.ROOT = root
            try:
                raw_base = root / "data" / "microstructure" / "raw" / "2026-08-26" / "bithumb" / "trade"
                raw_base.mkdir(parents=True)
                closed = raw_base / "bithumb_trade_krw-btc_2026-08-26_12.jsonl"
                active = raw_base / "bithumb_trade_krw-btc_2026-08-26_13.jsonl"
                closed.write_text("{}\n", encoding="utf-8")
                active.write_text("{}\n", encoding="utf-8")
                manifest = root / f"manifest_{closed.stem}.json"
                manifest.write_text(json.dumps({
                    "partition_path": str(closed.relative_to(root / "data")),
                    "bytes": closed.stat().st_size,
                    "schema_version": 4,
                    "monotonic_missing_count": 1,
                    "monotonic_invalid_count": 0,
                    "monotonic_reversal_count": 0,
                    "latency_parseable_observation_count": 0,
                    "latency_out_of_range_count": 0,
                    "exchange_timestamp_present_count": 0,
                }), encoding="utf-8")
                now = datetime(2026, 8, 26, 13, 30, tzinfo=timezone.utc)
                result = MODULE.manifest_coverage([closed, active], [manifest], now)
                self.assertEqual(result["closed_raw"], 1)
                self.assertEqual(result["covered"], 1)
                closed.write_text("{}\n{}\n", encoding="utf-8")
                result = MODULE.manifest_coverage([closed, active], [manifest], now)
                self.assertEqual(result["stale_or_mismatch"], 1)
                stopped = MODULE.manifest_coverage(
                    [closed, active], [manifest], now, collector_is_running=False
                )
                self.assertEqual(stopped["closed_raw"], 2)
                self.assertEqual(stopped["missing"], 1)

                manifest.write_text("[]", encoding="utf-8")
                non_object = MODULE.manifest_coverage([closed], [manifest], now)
                self.assertEqual(non_object["invalid_manifest_json"], 1)
                self.assertEqual(non_object["stale_or_mismatch"], 1)
            finally:
                MODULE.ROOT = old_root

    def test_tail_lines_is_bounded_and_returns_complete_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.jsonl"
            path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
            self.assertEqual(MODULE.tail_lines(path, 2, block_size=4), ["three", "four"])

    def test_tail_lines_has_hard_byte_cap_for_missing_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corrupt.jsonl"
            path.write_bytes(b"x" * 10_000)
            result = MODULE.tail_lines(path, 10, block_size=128, max_bytes=512)
            self.assertEqual(sum(len(line) for line in result), 512)

    def test_projection_subtracts_only_future_collection(self) -> None:
        gib = 1024**3
        projected_total, projected_free = MODULE.projected_free_at_target(
            current_free_bytes=100 * gib,
            total_bytes=20 * gib,
            elapsed_hours=24.0,
            target_hours=72.0,
        )
        self.assertEqual(projected_total, 60 * gib)
        self.assertEqual(projected_free, 60 * gib)

    def test_balanced_sampling_keeps_upbit_and_separates_streams(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            old_raw = MODULE.RAW_DIR
            MODULE.RAW_DIR = raw
            try:
                now = datetime.now(timezone.utc)
                paths = []
                for exchange, stream in (("bithumb", "trade"), ("upbit", "orderbook")):
                    path = raw / "2026-08-26" / exchange / stream / f"{exchange}_{stream}.jsonl"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    record = {
                        "exchange_ts": now.isoformat(),
                        "local_recv_ts": now.isoformat(),
                    }
                    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
                    paths.append(path)
                sampled = MODULE.sample_clock_offsets(paths, files_per_group=1, records_per_file=10)
                self.assertEqual(sampled[("bithumb", "trade")]["sample_count"], 1)
                self.assertEqual(sampled[("upbit", "orderbook")]["sample_count"], 1)
            finally:
                MODULE.RAW_DIR = old_raw

    def test_balanced_sampling_marks_stale_group_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            old_raw = MODULE.RAW_DIR
            MODULE.RAW_DIR = raw
            try:
                path = raw / "2026-08-26" / "upbit" / "trade" / "upbit_trade.jsonl"
                path.parent.mkdir(parents=True)
                now = datetime.now(timezone.utc)
                path.write_text(json.dumps({
                    "exchange_ts": now.isoformat(), "local_recv_ts": now.isoformat()
                }) + "\n", encoding="utf-8")
                old = time.time() - 10_000
                path.touch()
                import os
                os.utime(path, (old, old))
                sampled = MODULE.sample_clock_offsets(
                    [path], files_per_group=1, now_ts=time.time(), recent_seconds=60
                )
                self.assertEqual(sampled[("upbit", "trade")]["sample_count"], 0)
                self.assertIsNone(sampled[("upbit", "trade")]["newest_append_age_seconds"])
            finally:
                MODULE.RAW_DIR = old_raw

    def test_metrics_snapshot_fresh_stale_malformed_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collector_metrics.json"
            now = datetime.now(timezone.utc)
            self.assertEqual(MODULE.load_metrics_snapshot(path, now=now)["status"], "MISSING")
            path.write_text("not-json", encoding="utf-8")
            self.assertEqual(MODULE.load_metrics_snapshot(path, now=now)["status"], "MALFORMED")
            path.write_text(json.dumps({
                "schema_version": 1,
                "collector_run_id": "run-a",
                "process_id": 123,
                "written_at": now.isoformat(),
                "exchanges": {"bithumb": {"disconnect_count": 1}},
            }), encoding="utf-8")
            self.assertEqual(MODULE.load_metrics_snapshot(path, now=now)["status"], "FRESH")
            later = datetime.fromtimestamp(now.timestamp() + 30, tz=timezone.utc)
            stale = MODULE.load_metrics_snapshot(path, now=later)
            self.assertEqual(stale["status"], "STALE")
            self.assertEqual(stale["payload"]["exchanges"]["bithumb"]["disconnect_count"], 1)

            path.write_text(json.dumps({
                "schema_version": 1,
                "collector_run_id": "run-a",
                "process_id": 123,
                "written_at": now.isoformat(),
                "exchanges": {"bithumb": "not-an-object"},
            }), encoding="utf-8")
            self.assertEqual(MODULE.load_metrics_snapshot(path, now=now)["status"], "INVALID_SCHEMA")

            path.write_text(json.dumps({
                "schema_version": 1,
                "collector_run_id": "run-a",
                "process_id": 123,
                "exchanges": {},
            }), encoding="utf-8")
            self.assertEqual(MODULE.load_metrics_snapshot(path, now=now)["status"], "INVALID_SCHEMA")

    def test_metrics_mode_requires_one_matching_live_pid(self) -> None:
        snapshot = {
            "status": "FRESH",
            "payload": {"process_id": 123, "collector_run_id": "run-a", "exchanges": {}},
        }
        self.assertEqual(MODULE.metrics_counter_mode(snapshot, [123]), "LIVE")
        self.assertEqual(MODULE.metrics_counter_mode(snapshot, []), "HISTORICAL")
        self.assertEqual(MODULE.metrics_counter_mode(snapshot, [123, 456]), "HISTORICAL")
        self.assertEqual(MODULE.metrics_counter_mode(snapshot, [456]), "HISTORICAL")
        snapshot["status"] = "STALE"
        self.assertEqual(MODULE.metrics_counter_mode(snapshot, [123]), "HISTORICAL")

    def test_offset_sample_counts_only_values_outside_sixty_second_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            old_raw = MODULE.RAW_DIR
            MODULE.RAW_DIR = raw
            try:
                path = raw / "2026-08-26" / "bithumb" / "trade" / "bithumb_trade.jsonl"
                path.parent.mkdir(parents=True)
                exchange = datetime(2026, 8, 26, tzinfo=timezone.utc)
                offsets = (-60_001, -60_000, 60_000, 60_001)
                records = [
                    json.dumps({
                        "exchange_ts": exchange.isoformat(),
                        "local_recv_ts": datetime.fromtimestamp(
                            exchange.timestamp() + offset / 1000, tz=timezone.utc
                        ).isoformat(),
                    })
                    for offset in offsets
                ]
                path.write_text("\n".join(records) + "\n", encoding="utf-8")
                sample = MODULE.sample_clock_offsets(
                    [path], files_per_group=1, records_per_file=10
                )[("bithumb", "trade")]
                self.assertEqual(sample["sample_count"], 4)
                self.assertEqual(sample["offset_out_of_range_count"], 2)
            finally:
                MODULE.RAW_DIR = old_raw


if __name__ == "__main__":
    unittest.main()
