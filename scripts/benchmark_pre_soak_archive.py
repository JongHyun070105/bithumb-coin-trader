"""Measure local zstd streaming throughput on a synthetic microstructure fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import shutil
import tempfile
import time
from typing import Sequence

import zstandard

from bithumb_coin_trader.microstructure_io import iter_zstd_decompressed_chunks


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=50_000)
    parser.add_argument("--level", type=int, default=1)
    args = parser.parse_args(argv)
    if args.records < 1:
        parser.error("--records must be positive")

    with tempfile.TemporaryDirectory(prefix="pre-soak-benchmark-") as tmp:
        root = Path(tmp)
        raw = root / "fixture.jsonl"
        compressed = root / "fixture.jsonl.zst"
        with raw.open("w", encoding="utf-8") as handle:
            for index in range(args.records):
                handle.write(json.dumps({
                    "exchange": "binance",
                    "stream": "trade",
                    "market": "BTCUSDT",
                    "exchange_ts": "2026-09-01T00:00:00.000000+00:00",
                    "local_recv_ts": "2026-09-01T00:00:00.001000+00:00",
                    "local_write_ts": "2026-09-01T00:00:00.002000+00:00",
                    "payload": {"trade_id": index, "price": "108000.01", "quantity": "0.00125"},
                }, separators=(",", ":")) + "\n")
        raw_size = raw.stat().st_size

        started = time.perf_counter()
        with raw.open("rb") as source, compressed.open("wb") as target:
            with zstandard.ZstdCompressor(level=args.level).stream_writer(target, closefd=False) as writer:
                shutil.copyfileobj(source, writer, length=1024 * 1024)
        compression_seconds = time.perf_counter() - started

        restored_bytes = 0
        started = time.perf_counter()
        with compressed.open("rb") as source:
            for chunk in iter_zstd_decompressed_chunks(source):
                restored_bytes += len(chunk)
        decompression_seconds = time.perf_counter() - started
        if restored_bytes != raw_size:
            raise RuntimeError("benchmark restore byte count mismatch")
        mib = 1024 * 1024
        result = {
            "scope": "LOCAL_MEASURED_ONLY",
            "records": args.records,
            "compression_level": args.level,
            "raw_bytes": raw_size,
            "compressed_bytes": compressed.stat().st_size,
            "compressed_ratio_percent": round(100.0 * compressed.stat().st_size / raw_size, 4),
            "compression_seconds": round(compression_seconds, 6),
            "compression_mib_per_second": round(raw_size / mib / compression_seconds, 2),
            "decompression_seconds": round(decompression_seconds, 6),
            "decompression_mib_per_second": round(raw_size / mib / decompression_seconds, 2),
            "process_max_rss_platform_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
