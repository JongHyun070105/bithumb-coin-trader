from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_offline_manifests.py"
SPEC = importlib.util.spec_from_file_location("generate_offline_manifests", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OfflineManifestTests(unittest.TestCase):
    def test_only_prior_utc_hours_are_finalized(self) -> None:
        now = datetime(2026, 8, 26, 13, 30, tzinfo=timezone.utc)
        closed = Path("binance_trade_btcusdt_2026-08-26_12.jsonl")
        active = Path("binance_trade_btcusdt_2026-08-26_13.jsonl")
        self.assertTrue(MODULE.is_closed_partition(closed, now))
        self.assertFalse(MODULE.is_closed_partition(active, now))

    def test_manifest_size_drift_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "data" / "microstructure" / "raw" / "2026-08-26" / "bithumb" / "trade" / "bithumb_trade_krw-btc_2026-08-26_12.jsonl"
            raw.parent.mkdir(parents=True)
            raw.write_text("{}\n", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "partition_path": str(raw.relative_to(root / "data")),
                "bytes": raw.stat().st_size,
                "sha256": "0" * 64,
                "schema_version": 4,
                "monotonic_missing_count": 1,
                "monotonic_invalid_count": 0,
                "monotonic_reversal_count": 0,
                "latency_parseable_observation_count": 0,
                "latency_out_of_range_count": 0,
                "exchange_timestamp_present_count": 0,
            }), encoding="utf-8")
            old_root = MODULE.ROOT
            MODULE.ROOT = root
            try:
                self.assertTrue(MODULE.manifest_matches_raw(raw, manifest))
                raw.write_text("{}\n{}\n", encoding="utf-8")
                self.assertFalse(MODULE.manifest_matches_raw(raw, manifest))
            finally:
                MODULE.ROOT = old_root

    def test_obsolete_manifest_schema_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "data" / "microstructure" / "raw" / "2026-08-26" / "bithumb" / "trade" / "bithumb_trade_krw-btc_2026-08-26_12.jsonl"
            raw.parent.mkdir(parents=True)
            raw.write_text("{}\n", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "partition_path": str(raw.relative_to(root / "data")),
                "bytes": raw.stat().st_size,
                "sha256": "0" * 64,
                "schema_version": 3,
            }), encoding="utf-8")
            old_root = MODULE.ROOT
            MODULE.ROOT = root
            try:
                self.assertFalse(MODULE.manifest_matches_raw(raw, manifest))
            finally:
                MODULE.ROOT = old_root


if __name__ == "__main__":
    unittest.main()
