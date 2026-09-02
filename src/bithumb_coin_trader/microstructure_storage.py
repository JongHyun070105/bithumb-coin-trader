"""Append-only local microstructure raw storage and quarantine engine.

Enforces:
1. Partitioned JSONL persistence: data/microstructure/raw/YYYY-MM-DD/{exchange}/{stream}/
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
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
RAW_BASE_DIR = ROOT / "data" / "microstructure" / "raw"
QUARANTINE_BASE_DIR = ROOT / "data" / "microstructure" / "quarantine"
MANIFESTS_BASE_DIR = ROOT / "data" / "microstructure" / "manifests"

logger = logging.getLogger("bithumb_coin_trader.microstructure_storage")


@dataclass(frozen=True)
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
    trade_sequence_gaps: int | None
    trade_duplicate_count: int
    trade_sequence_completeness: str
    malformed_quarantined_count: int
    schema_mismatch_count: int
    missing_required_field_count: int
    non_finite_numeric_count: int
    malformed_timestamp_count: int
    local_timestamp_reversal_count: int
    unknown_market_count: int
    monotonic_missing_count: int
    monotonic_invalid_count: int
    monotonic_reversal_count: int
    latency_observation_count: int
    latency_parseable_observation_count: int
    latency_out_of_range_count: int
    exchange_timestamp_present_count: int
    latency_sample_count: int
    latency_metric_semantics: str
    collector_version: str = "v9.1.0-quarantine-hardened"
    git_commit: str = "HEAD"
    schema_version: int = 4

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RawMicrostructureStorage:
    """Manages immutable raw partition files, quarantine stores, and verifiable manifests."""

    def __init__(
        self,
        base_dir: Path | None = None,
        *,
        quarantine_dir: Path | None = None,
        manifest_dir: Path | None = None,
        git_commit: str = "HEAD",
    ) -> None:
        self.base_dir = base_dir or RAW_BASE_DIR
        self.quarantine_dir = quarantine_dir or (
            (base_dir.parent / "quarantine") if base_dir else QUARANTINE_BASE_DIR
        )
        self.manifest_dir = manifest_dir or (
            (base_dir.parent / "manifests") if base_dir else MANIFESTS_BASE_DIR
        )
        if git_commit != "HEAD" and (
            len(git_commit) != 40 or any(character not in "0123456789abcdef" for character in git_commit)
        ):
            raise ValueError("git_commit must be HEAD or an exact lowercase 40-character commit")
        self.git_commit = git_commit
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
        local_receive_monotonic_ns: int | None = None,
        collector_run_id: str | None = None,
    ) -> Path:
        now_write = datetime.now(timezone.utc)
        record = {
            "exchange": exchange,
            "stream": stream,
            "market": market,
            "exchange_ts": exchange_ts.isoformat() if exchange_ts else None,
            "local_recv_ts": local_receive_ts.isoformat(),
            "local_recv_monotonic_ns": local_receive_monotonic_ns,
            "collector_run_id": collector_run_id,
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
        latency_observation_count = 0
        latency_parseable_observation_count = 0
        latency_out_of_range_count = 0
        exchange_timestamp_present_count = 0
        negative_latency_observation_count = 0
        latency_sample_limit = 100_000
        latency_rng = random.Random(0)
        exchange = ""
        stream = ""
        market = ""
        seen_seqs: set[int] = set()
        dup_count = 0
        malformed_count = 0
        schema_mismatch_count = 0
        missing_required_field_count = 0
        non_finite_numeric_count = 0
        malformed_timestamp_count = 0
        local_timestamp_reversal_count = 0
        unknown_market_count = 0
        monotonic_missing_count = 0
        monotonic_invalid_count = 0
        monotonic_reversal_count = 0
        previous_monotonic_by_run: dict[str, int] = {}
        previous_local_dt: datetime | None = None
        required_fields = {
            "exchange", "stream", "market", "exchange_ts", "local_recv_ts", "local_write_ts", "payload"
        }
        path_parts = file_path.relative_to(self.base_dir).parts
        expected_exchange = path_parts[1] if len(path_parts) >= 4 else None
        expected_stream = path_parts[2] if len(path_parts) >= 4 else None

        with file_path.open("rb") as f:
            for raw_line in f:
                hasher.update(raw_line)
                count += 1
                try:
                    line = raw_line.decode("utf-8", errors="strict")
                    non_finite_in_record = 0

                    def mark_non_finite(_: str) -> None:
                        nonlocal non_finite_in_record
                        non_finite_in_record += 1
                        return None

                    rec = json.loads(line, parse_float=str, parse_constant=mark_non_finite)
                    non_finite_numeric_count += non_finite_in_record
                    if not isinstance(rec, dict):
                        schema_mismatch_count += 1
                        continue
                    missing_required_field_count += len(required_fields - rec.keys())
                    exchange = rec.get("exchange", "")
                    stream = rec.get("stream", "")
                    market = rec.get("market", "")
                    payload = rec.get("payload", {})
                    if (
                        not isinstance(payload, dict)
                        or exchange != expected_exchange
                        or stream != expected_stream
                        or not isinstance(market, str)
                    ):
                        schema_mismatch_count += 1
                    if str(market).upper() == "UNKNOWN":
                        unknown_market_count += 1
                    e_ts = rec.get("exchange_ts")
                    l_ts = rec.get("local_recv_ts")
                    monotonic_ns = rec.get("local_recv_monotonic_ns")
                    run_id = rec.get("collector_run_id")
                    if monotonic_ns is None:
                        monotonic_missing_count += 1
                    elif not isinstance(monotonic_ns, int) or not isinstance(run_id, str) or not run_id:
                        monotonic_invalid_count += 1
                    else:
                        previous_monotonic = previous_monotonic_by_run.get(run_id)
                        if previous_monotonic is not None and monotonic_ns < previous_monotonic:
                            monotonic_reversal_count += 1
                        previous_monotonic_by_run[run_id] = monotonic_ns

                    if first_exch is None and e_ts:
                        first_exch = e_ts
                    if e_ts:
                        last_exch = e_ts
                    if first_loc is None and l_ts:
                        first_loc = l_ts
                    if l_ts:
                        last_loc = l_ts

                    try:
                        local_dt = datetime.fromisoformat(l_ts) if isinstance(l_ts, str) else None
                        write_ts = rec.get("local_write_ts")
                        write_dt = datetime.fromisoformat(write_ts) if isinstance(write_ts, str) else None
                        if local_dt is None or write_dt is None:
                            raise ValueError("missing local timestamp")
                        if previous_local_dt is not None and local_dt < previous_local_dt:
                            local_timestamp_reversal_count += 1
                        previous_local_dt = local_dt
                        if e_ts is not None:
                            if not isinstance(e_ts, str):
                                raise ValueError("invalid exchange timestamp")
                            datetime.fromisoformat(e_ts)
                    except (TypeError, ValueError):
                        malformed_timestamp_count += 1

                    if e_ts and l_ts:
                        exchange_timestamp_present_count += 1
                        try:
                            dt_e = datetime.fromisoformat(e_ts)
                            dt_l = datetime.fromisoformat(l_ts)
                            lat_ms = (dt_l - dt_e).total_seconds() * 1000.0
                            latency_parseable_observation_count += 1
                            if lat_ms < 0:
                                negative_latency_observation_count += 1
                            if -60_000.0 < lat_ms < 60_000.0:
                                latency_observation_count += 1
                                if len(latencies_ms) < latency_sample_limit:
                                    latencies_ms.append(lat_ms)
                                else:
                                    index = latency_rng.randrange(latency_observation_count)
                                    if index < latency_sample_limit:
                                        latencies_ms[index] = lat_ms
                            else:
                                latency_out_of_range_count += 1
                        except Exception:
                            pass

                    # Stream-specific sequence integrity (only on trade streams with sequence IDs)
                    seq_id = payload.get("sequential_id") or payload.get("sequence") or payload.get("trade_id")
                    if seq_id is not None and stream == "trade":
                        try:
                            seq_int = int(seq_id)
                            if seq_int in seen_seqs:
                                dup_count += 1
                            else:
                                seen_seqs.add(seq_int)
                        except (TypeError, ValueError):
                            pass
                except (UnicodeDecodeError, json.JSONDecodeError):
                    malformed_count += 1

        neg_lat_count = negative_latency_observation_count
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
            trade_sequence_gaps=None,
            trade_duplicate_count=dup_count,
            trade_sequence_completeness="not_directly_verifiable",
            malformed_quarantined_count=malformed_count,
            schema_mismatch_count=schema_mismatch_count,
            missing_required_field_count=missing_required_field_count,
            non_finite_numeric_count=non_finite_numeric_count,
            malformed_timestamp_count=malformed_timestamp_count,
            local_timestamp_reversal_count=local_timestamp_reversal_count,
            unknown_market_count=unknown_market_count,
            monotonic_missing_count=monotonic_missing_count,
            monotonic_invalid_count=monotonic_invalid_count,
            monotonic_reversal_count=monotonic_reversal_count,
            latency_observation_count=latency_observation_count,
            latency_parseable_observation_count=latency_parseable_observation_count,
            latency_out_of_range_count=latency_out_of_range_count,
            exchange_timestamp_present_count=exchange_timestamp_present_count,
            latency_sample_count=len(latencies_ms),
            latency_metric_semantics="local_receive_minus_exchange_labelled_timestamp_not_network_latency",
            git_commit=self.git_commit,
        )

        manifest_file = self.manifest_dir / f"manifest_{file_path.stem}.json"
        temporary_manifest = manifest_file.with_suffix(".json.tmp")
        with temporary_manifest.open("w", encoding="utf-8") as mf:
            json.dump(manifest.to_dict(), mf, indent=2)
            mf.flush()
        temporary_manifest.replace(manifest_file)

        return manifest
