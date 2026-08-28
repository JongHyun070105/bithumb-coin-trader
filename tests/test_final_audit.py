from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import generate_72h_final_audit as final_audit


class FinalAuditTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        raw_dir = root / "data" / "microstructure" / "raw"
        manifest_dir = root / "data" / "microstructure" / "manifests"
        quarantine_dir = root / "data" / "microstructure" / "quarantine"
        raw = raw_dir / "2026-08-28" / "binance" / "orderbook" / "part.jsonl"
        raw.parent.mkdir(parents=True)
        raw.write_text('{"market":"UNKNOWN"}\n', encoding="utf-8")
        manifest_dir.mkdir(parents=True)
        payload = {
            "partition_path": str(raw.relative_to(root / "data")),
            "schema_version": 4,
            "sha256": "a" * 64,
            "exchange": "binance",
            "stream": "orderbook",
            "record_count": 1,
            "bytes": raw.stat().st_size,
        }
        payload.update({manifest_name: 0 for manifest_name in final_audit.COUNTER_FIELDS.values()})
        payload.update({"record_count": 1, "bytes": raw.stat().st_size, "unknown_market_count": 1, "monotonic_missing_count": 1})
        (manifest_dir / "manifest_part.json").write_text(json.dumps(payload), encoding="utf-8")
        return raw_dir, manifest_dir, quarantine_dir

    def test_report_is_fail_closed_for_v9_findings_and_missing_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_dir, manifest_dir, quarantine_dir = self._fixture(root)
            with (
                patch.object(final_audit, "ROOT", root),
                patch.object(final_audit, "RAW_DIR", raw_dir),
                patch.object(final_audit, "MANIFEST_DIR", manifest_dir),
                patch.object(final_audit, "QUARANTINE_DIR", quarantine_dir),
            ):
                report = final_audit.build_report()
            self.assertEqual(report["status"]["manifest_integrity"], "PASS")
            self.assertEqual(report["status"]["binance_orderbook_identity"], "FAIL")
            self.assertFalse(report["status"]["alpha_research_ready"])
            self.assertFalse(report["status"]["live_trading_ready"])
            self.assertIn("NOT_VERIFIABLE", report["operational_evidence"]["queue_dropped_events"])

    def test_missing_manifest_fails_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_dir = root / "data" / "microstructure" / "raw"
            raw = raw_dir / "2026-08-28" / "upbit" / "trade" / "missing.jsonl"
            raw.parent.mkdir(parents=True)
            raw.write_text("{}\n", encoding="utf-8")
            manifest_dir = root / "data" / "microstructure" / "manifests"
            manifest_dir.mkdir(parents=True)
            with (
                patch.object(final_audit, "ROOT", root),
                patch.object(final_audit, "RAW_DIR", raw_dir),
                patch.object(final_audit, "MANIFEST_DIR", manifest_dir),
            ):
                report = final_audit.build_report()
            self.assertEqual(report["status"]["manifest_integrity"], "FAIL")


if __name__ == "__main__":
    unittest.main()
