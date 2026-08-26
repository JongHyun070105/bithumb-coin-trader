"""Immutable Append-Only Microstructure Raw Storage & Quarantine Engine.

Enforces:
1. Lossless partitioned JSONL storage: data/microstructure/raw/YYYY-MM-DD/{exchange}/{stream}/
2. Quarantine storage: data/microstructure/quarantine/YYYY-MM-DD/ (preserves raw bytes & error reasons)
3. 3-level timestamps: exchange_ts, local_receive_ts, local_write_ts
4. Deterministic SHA-256 Manifest generation per partition with stream-specific integrity metrics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
RAW_BASE_DIR = ROOT / "data" / "microstructure" / "raw"
QUARANTINE_BASE_DIR = ROOT / "data" / "microstructure" / "quarantine"
MANIFESTS_BASE_DIR = ROOT / "data" / "microstructure" / "manifests"

logger = logging.getLogger("bithumb_coin_trader.microstructure_storage")


@dataclass(frozen=True, slots=True)
class PartitionManifest:
    partition_path: str
    exchange: str
    stream: str
    market: str
    record_count: int
    first_exchange_ts: str | None
    last_exchange_ts: str | None
    first_local_ts: str | None
    last_local_ts: str | None
    sha256: str
    bytes: int
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_max_ms: float
    clock_skew_or_offset_p50_ms: float
    negative_latency_count: int
    trade_sequence_gaps: int
    trade_duplicate_count: int
    malformed_quarantined_count: int
    collector_version: str = "v9.1.0-quarantine-hardened"
    git_commit: str = "HEAD"
    schema_version: int = 3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RawMicrostructureStorage:
    """Manages immutable raw partition files, quarantine stores, and verifiable manifests."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or RAW_BASE_DIR
        self.quarantine_dir = (base_dir.parent / "quarantine") if base_dir else QUARANTINE_BASE_DIR
        self.manifest_dir = (base_dir.parent / "manifests") if base_dir else MANIFESTS_BASE_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

    def get_partition_file(self, exchange: str, stream: str, market: str, ts: datetime) -> Path:
        dt_str = ts.strftime("%Y-%m-%d")
        hour_str = ts.strftime("%H")
        clean_market = market.replace("/", "-").replace(":", "-").lower()
        part_dir = self.base_dir / dt_str / exchange.lower() / stream.lower()
        part_dir.mkdir(parents=True, exist_ok=True)
        return part_dir / f"{exchange.lower()}_{stream.lower()}_{clean_market}_{dt_str}_{hour_str}.jsonl"

    def append_raw_record(
        self,
        exchange: str,
        stream: str,
        market: str,
        payload: dict[str, Any],
        local_receive_ts: datetime,
        exchange_ts: datetime | None = None,
    ) -> Path:
        now_write = datetime.now(timezone.utc)
        record = {
            "exchange": exchange,
            "stream": stream,
            "market": market,
            "exchange_ts": exchange_ts.isoformat() if exchange_ts else None,
            "local_recv_ts": local_receive_ts.isoformat(),
            "local_write_ts": now_write.isoformat(),
            "payload": payload,
        }
        file_path = self.get_partition_file(exchange, stream, market, now_write)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with file_path.open("a", encoding="utf-8") as f:
            f.write(line)
        return file_path

    def quarantine_malformed_record(
        self,
        exchange: str,
        raw_bytes: bytes,
        error_reason: str,
        local_receive_ts: datetime,
    ) -> Path:
        """Preserves raw unparsable bytes in quarantine without silent dropping."""
        now = datetime.now(timezone.utc)
        dt_str = now.strftime("%Y-%m-%d")
        q_dir = self.quarantine_dir / dt_str
        q_dir.mkdir(parents=True, exist_ok=True)
        q_file = q_dir / f"quarantine_{exchange.lower()}_{dt_str}.jsonl"

        record = {
            "exchange": exchange,
            "local_recv_ts": local_receive_ts.isoformat(),
            "quarantined_at": now.isoformat(),
            "error_reason": error_reason,
            "raw_payload_hex": raw_bytes.hex(),
            "raw_payload_text": raw_bytes.decode("utf-8", errors="replace"),
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with q_file.open("a", encoding="utf-8") as f:
            f.write(line)
        return q_file

    def generate_partition_manifest(self, file_path: Path) -> PartitionManifest:
        if not file_path.exists():
            raise FileNotFoundError(f"Partition file does not exist: {file_path}")

        hasher = hashlib.sha256()
        count = 0
        total_bytes = file_path.stat().st_size
        first_exch: str | None = None
        last_exch: str | None = None
        first_loc: str | None = None
        last_loc: str | None = None
        latencies_ms: list[float] = []
        exchange = ""
        stream = ""
        market = ""
        seen_seqs: set[int] = set()
        dup_count = 0
        gap_count = 0
        last_seq: int | None = None
        malformed_count = 0

        with file_path.open("r", encoding="utf-8") as f:
            for line in f:
                hasher.update(line.encode("utf-8"))
                count += 1
                try:
                    rec = json.loads(line)
                    exchange = rec.get("exchange", "")
                    stream = rec.get("stream", "")
                    market = rec.get("market", "")
                    e_ts = rec.get("exchange_ts")
                    l_ts = rec.get("local_recv_ts")

                    if first_exch is None and e_ts:
                        first_exch = e_ts
                    if e_ts:
                        last_exch = e_ts
                    if first_loc is None and l_ts:
                        first_loc = l_ts
                    if l_ts:
                        last_loc = l_ts

                    if e_ts and l_ts:
                        try:
                            dt_e = datetime.fromisoformat(e_ts)
                            dt_l = datetime.fromisoformat(l_ts)
                            lat_ms = (dt_l - dt_e).total_seconds() * 1000.0
                            if -60_000.0 < lat_ms < 60_000.0:
                                latencies_ms.append(lat_ms)
                        except Exception:
                            pass

                    # Stream-specific sequence integrity (only on trade streams with sequence IDs)
                    payload = rec.get("payload", {})
                    seq_id = payload.get("sequential_id") or payload.get("sequence") or payload.get("trade_id")
                    if seq_id is not None and stream == "trade":
                        try:
                            seq_int = int(seq_id)
                            if seq_int in seen_seqs:
                                dup_count += 1
                            else:
                                seen_seqs.add(seq_int)
                            if last_seq is not None and seq_int > last_seq + 1:
                                gap_count += (seq_int - last_seq - 1)
                            last_seq = seq_int
                        except ValueError:
                            pass
                except json.JSONDecodeError:
                    malformed_count += 1

        neg_lat_count = sum(1 for x in latencies_ms if x < 0)
        pos_latencies = [x for x in latencies_ms if x >= 0]

        clock_skew_p50 = sorted(latencies_ms)[int(len(latencies_ms) * 0.50)] if latencies_ms else 0.0

        if pos_latencies:
            sorted_pos = sorted(pos_latencies)
            p50 = sorted_pos[int(len(sorted_pos) * 0.50)]
            p95 = sorted_pos[int(len(sorted_pos) * 0.95)]
            p99 = sorted_pos[int(len(sorted_pos) * 0.99)]
            lat_max = sorted_pos[-1]
        elif latencies_ms:
            p50 = clock_skew_p50
            p95 = p99 = lat_max = sorted(latencies_ms)[-1]
        else:
            p50 = p95 = p99 = lat_max = 0.0

        manifest = PartitionManifest(
            partition_path=str(file_path.relative_to(self.base_dir.parent.parent)),
            exchange=exchange,
            stream=stream,
            market=market,
            record_count=count,
            first_exchange_ts=first_exch,
            last_exchange_ts=last_exch,
            first_local_ts=first_loc,
            last_local_ts=last_loc,
            sha256=hasher.hexdigest(),
            bytes=total_bytes,
            latency_p50_ms=round(p50, 2),
            latency_p95_ms=round(p95, 2),
            latency_p99_ms=round(p99, 2),
            latency_max_ms=round(lat_max, 2),
            clock_skew_or_offset_p50_ms=round(clock_skew_p50, 2),
            negative_latency_count=neg_lat_count,
            trade_sequence_gaps=gap_count,
            trade_duplicate_count=dup_count,
            malformed_quarantined_count=malformed_count,
        )

        manifest_file = self.manifest_dir / f"manifest_{file_path.stem}.json"
        with manifest_file.open("w", encoding="utf-8") as mf:
            json.dump(manifest.to_dict(), mf, indent=2)

        return manifest
