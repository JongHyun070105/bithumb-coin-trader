from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import zstandard

from bithumb_coin_trader.microstructure_io import CompressedInputError, scan_jsonl


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_raw_integrity_offline.py"
SPEC = importlib.util.spec_from_file_location("audit_raw_integrity_offline", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class ZstdFullScanTests(unittest.TestCase):
    def _raw(self, root: Path, count: int = 100) -> Path:
        path = root / "fixture.jsonl"
        rows = []
        for index in range(count):
            rows.append(json.dumps({
                "exchange": "binance",
                "stream": "trade",
                "market": "BTCUSDT" if index else "UNKNOWN",
                "exchange_ts": "2026-09-01T00:00:00+00:00",
                "local_recv_ts": "2026-09-01T00:00:00.001000+00:00",
                "local_write_ts": "2026-09-01T00:00:00.002000+00:00",
                "payload": {"trade_id": index},
            }, separators=(",", ":")))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return path

    def test_raw_and_zstd_full_scan_are_logically_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = self._raw(root)
            compressed = root / "fixture.jsonl.zst"
            compressed.write_bytes(zstandard.ZstdCompressor(level=1).compress(raw.read_bytes()))
            raw_result = scan_jsonl(raw).to_dict()
            compressed_result = scan_jsonl(compressed).to_dict()
            for result in (raw_result, compressed_result):
                result.pop("path")
                result.pop("compression")
            self.assertEqual(raw_result, compressed_result)
            self.assertEqual(raw_result["records"], 100)
            self.assertEqual(raw_result["unknown_market"], 1)
            self.assertEqual(raw_result["valid_records"], 99)

    def test_quality_findings_make_full_scan_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._raw(Path(tmp), count=2)
            report = AUDIT.full_scan([raw])
            self.assertEqual(report["totals"]["unknown_market"], 1)
            self.assertEqual(report["totals"]["status"], "FAIL")

    def test_corrupted_and_truncated_zstd_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = self._raw(root)
            encoded = zstandard.ZstdCompressor(level=1).compress(raw.read_bytes())
            for name, data in (("corrupt.jsonl.zst", b"not-zstd"), ("truncated.jsonl.zst", encoded[:-4])):
                path = root / name
                path.write_bytes(data)
                with self.assertRaises(CompressedInputError):
                    scan_jsonl(path)

    def test_full_scan_reports_compressed_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = self._raw(root)
            bad = root / "bad.jsonl.zst"
            bad.write_bytes(b"bad")
            report = AUDIT.full_scan([raw, bad])
            self.assertEqual(report["totals"]["status"], "FAIL")
            self.assertEqual(report["totals"]["scan_failures"], 1)

    def test_oversized_unterminated_line_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "huge.jsonl"
            path.write_bytes(b"x" * (17 * 1024 * 1024))
            with self.assertRaisesRegex(ValueError, "bounded"):
                scan_jsonl(path)


if __name__ == "__main__":
    unittest.main()
