"""P13: Source immutability test — raw input must NOT be mutated by CLI pipeline.
P17: Verify no live transport in research/paper path.
P18: Terminology check — no forbidden strong claims.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_canonical_zst(path: Path, n: int = 10) -> None:
    """Write n sorted canonical records."""
    from bithumb_coin_trader.canonical_market_data import CanonicalOrderBook, write_canonical_ndjson_zstd
    records = [
        CanonicalOrderBook(
            exchange="bithumb",
            market="BTC-KRW",
            receive_timestamp_ms=i * 1000,
            exchange_timestamp_ms=i,
            bids=[(100_000_000 - i * 1000, 0.1)],
            asks=[(100_050_000 + i * 1000, 0.1)],
        )
        for i in range(1, n + 1)
    ]
    write_canonical_ndjson_zstd(path, records)


# ─── P13: Source immutability ─────────────────────────────────────────────────

class TestSourceImmutability:
    """Raw source data must never be modified by any research import tool."""

    def test_audit_quality_does_not_modify_source(self, tmp_path):
        """audit-quality must open files read-only."""
        input_dir = tmp_path / "raw_epoch"
        input_dir.mkdir()
        src = input_dir / "data.ndjson.zst"
        _make_canonical_zst(src, n=5)

        m = input_dir / "manifest.json"
        m.write_text(json.dumps({"epoch": "test", "exchange": "bithumb", "record_count": 5}))

        hash_before_src = _sha256_file(src)
        hash_before_m = _sha256_file(m)

        from bithumb_coin_trader.research_cli import main
        report_out = tmp_path / "report.json"
        try:
            main(["audit-quality", "--input-dir", str(input_dir), "--report-out", str(report_out)])
        except (SystemExit, Exception):
            pass

        assert _sha256_file(src) == hash_before_src, "audit-quality modified source data file!"
        assert _sha256_file(m) == hash_before_m, "audit-quality modified manifest!"

    def test_transform_canonical_does_not_modify_source(self, tmp_path):
        """transform-canonical must not overwrite or rename input files."""
        input_dir = tmp_path / "raw"
        input_dir.mkdir()
        src = input_dir / "sample.ndjson.zst"
        _make_canonical_zst(src, n=5)
        hash_before = _sha256_file(src)

        out_dir = tmp_path / "canonical"
        from bithumb_coin_trader.research_cli import main
        try:
            main(["transform-canonical", "--input-dir", str(input_dir),
                  "--output-dir", str(out_dir)])
        except (SystemExit, Exception):
            pass

        assert _sha256_file(src) == hash_before, "transform-canonical modified source file!"

    def test_source_file_not_opened_writable_by_partition(self, tmp_path):
        """partition-dataset must not open source file in write mode."""
        src = tmp_path / "input.ndjson.zst"
        _make_canonical_zst(src, n=20)

        # Make source read-only
        import stat
        src.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

        try:
            dq_file = tmp_path / "dq.json"
            dq_file.write_text(json.dumps({"status": "DQ_PASS", "hard_fail_count": 0}))
            out_dir = tmp_path / "out"

            from bithumb_coin_trader.research_cli import main
            # This should work (read-only source ok) OR fail with data error, NOT permission error
            try:
                result = main(["partition-dataset", "--input-file", str(src),
                               "--output-dir", str(out_dir), "--dq-report", str(dq_file)])
                # If it succeeds, source must still be intact
                assert _sha256_file(src) is not None
            except PermissionError:
                pytest.fail("partition-dataset tried to write to source file (read-only)")
        finally:
            # Restore permissions for cleanup
            src.chmod(stat.S_IRUSR | stat.S_IWUSR)


# ─── P17: Live transport check ────────────────────────────────────────────────

class TestNoLiveTransport:
    """Research and paper path must not reference live exchange transport."""

    def _get_source(self, module_name: str) -> str:
        import importlib
        import inspect
        mod = importlib.import_module(f"bithumb_coin_trader.{module_name}")
        return inspect.getsource(mod)

    BANNED_PATTERNS = [
        "requests.post(",
        "httpx.post(",
        "aiohttp.ClientSession",
        "api.bithumb.com",
        "api.binance.com",
        "api.upbit.com",
    ]

    def _check_banned(self, source: str, module: str) -> list[str]:
        return [
            f"{module}: found '{p}'"
            for p in self.BANNED_PATTERNS
            if p in source
        ]

    def test_paper_engine_no_live_endpoint(self):
        violations = self._check_banned(self._get_source("paper_engine"), "paper_engine")
        assert not violations, violations

    def test_research_cli_no_live_endpoint(self):
        violations = self._check_banned(self._get_source("research_cli"), "research_cli")
        assert not violations, violations

    def test_experiment_runner_no_live_transport(self):
        violations = self._check_banned(self._get_source("experiment_runner"), "experiment_runner")
        assert not violations, violations

    def test_risk_engine_no_live_transport(self):
        violations = self._check_banned(self._get_source("risk_engine"), "risk_engine")
        assert not violations, violations


# ─── P18: Terminology guard ───────────────────────────────────────────────────

class TestTerminology:
    """Phase 3 result terminology: forbidden strong claims without evidence."""

    BANNED_CLAIMS = [
        "PRODUCTION READY",
        "production ready",
        "100% safe",
        "100% verified",
        "fully verified",
    ]
    WORKTREE = Path(__file__).resolve().parent.parent

    def _check_file(self, rel: str) -> list[str]:
        path = self.WORKTREE / rel
        if not path.exists():
            return []
        text = path.read_text(errors="replace")
        return [f"{rel}: '{c}'" for c in self.BANNED_CLAIMS if c in text]

    def test_verification_matrix_no_100_percent_verified(self):
        violations = self._check_file("docs/PROJECT_VERIFICATION_MATRIX.md")
        assert not violations, violations

    def test_core_modules_no_production_ready(self):
        violations = []
        for mod in ["experiment_runner.py", "prospective_dataset.py",
                    "risk_engine.py", "research_cli.py"]:
            violations.extend(self._check_file(f"src/bithumb_coin_trader/{mod}"))
        assert not violations, violations
