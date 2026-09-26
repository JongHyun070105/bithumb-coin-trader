"""Canonical ingestion pipeline and partitioned Parquet storage for external BitMEX dataset.

Enforces:
- Separation into external_execution, external_funding, external_settlement, external_wallet
- Explicit provenance retained on every row (dataset_id, source_file, source_row_number, source_sha256, ingestion_version)
- Canonical event ordering based on transact_time and timestamp, not raw file ordering
- Deterministic partitioned Parquet derived dataset
- Memory-efficient batch processing
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from bithumb_coin_trader.research_infra.external_expert import (
    _decimal,
    _first,
    _timestamp,
    sha256_file,
)
from bithumb_coin_trader.research_infra.registry import EXTERNAL_BITMEX_DATASET_ID

INGESTION_VERSION = "1.0.0"

EXECUTION_SCHEMA = pa.schema([
    ("dataset_id", pa.string()),
    ("execution_id", pa.string()),
    ("order_id", pa.string()),
    ("trade_match_id", pa.string()),
    ("transact_time", pa.timestamp("us", tz="UTC")),
    ("timestamp", pa.timestamp("us", tz="UTC")),
    ("symbol", pa.string()),
    ("side", pa.string()),
    ("price", pa.float64()),
    ("quantity", pa.float64()),
    ("execution_type", pa.string()),
    ("order_type", pa.string()),
    ("order_status", pa.string()),
    ("liquidity_indicator", pa.string()),
    ("fee", pa.float64()),
    ("fee_currency", pa.string()),
    ("exec_cost", pa.float64()),
    ("home_notional", pa.float64()),
    ("foreign_notional", pa.float64()),
    ("order_qty", pa.float64()),
    ("leaves_qty", pa.float64()),
    ("cum_qty", pa.float64()),
    ("avg_px", pa.float64()),
    ("source_file", pa.string()),
    ("source_row_number", pa.int64()),
    ("source_sha256", pa.string()),
    ("ingestion_version", pa.string()),
    ("year", pa.int32()),
    ("month", pa.int32()),
])

FUNDING_SCHEMA = pa.schema([
    ("dataset_id", pa.string()),
    ("execution_id", pa.string()),
    ("transact_time", pa.timestamp("us", tz="UTC")),
    ("timestamp", pa.timestamp("us", tz="UTC")),
    ("symbol", pa.string()),
    ("fee", pa.float64()),
    ("fee_currency", pa.string()),
    ("exec_cost", pa.float64()),
    ("home_notional", pa.float64()),
    ("foreign_notional", pa.float64()),
    ("source_file", pa.string()),
    ("source_row_number", pa.int64()),
    ("source_sha256", pa.string()),
    ("ingestion_version", pa.string()),
    ("year", pa.int32()),
    ("month", pa.int32()),
])

SETTLEMENT_SCHEMA = pa.schema([
    ("dataset_id", pa.string()),
    ("execution_id", pa.string()),
    ("transact_time", pa.timestamp("us", tz="UTC")),
    ("timestamp", pa.timestamp("us", tz="UTC")),
    ("symbol", pa.string()),
    ("price", pa.float64()),
    ("quantity", pa.float64()),
    ("fee", pa.float64()),
    ("fee_currency", pa.string()),
    ("source_file", pa.string()),
    ("source_row_number", pa.int64()),
    ("source_sha256", pa.string()),
    ("ingestion_version", pa.string()),
    ("year", pa.int32()),
    ("month", pa.int32()),
])

WALLET_SCHEMA = pa.schema([
    ("dataset_id", pa.string()),
    ("transact_id", pa.string()),
    ("date", pa.string()),
    ("transact_time", pa.timestamp("us", tz="UTC")),
    ("timestamp", pa.timestamp("us", tz="UTC")),
    ("event_type", pa.string()),
    ("amount_satoshi", pa.int64()),
    ("amount_xbt", pa.float64()),
    ("currency", pa.string()),
    ("wallet_balance_satoshi", pa.int64()),
    ("wallet_balance_xbt", pa.float64()),
    ("address", pa.string()),
    ("network", pa.string()),
    ("tx", pa.string()),
    ("source_file", pa.string()),
    ("source_row_number", pa.int64()),
    ("source_sha256", pa.string()),
    ("ingestion_version", pa.string()),
    ("year", pa.int32()),
    ("month", pa.int32()),
])


def _parse_wallet_datetime(date_str: str | None, time_str: str | None) -> datetime | None:
    if not date_str:
        return _timestamp(time_str)
    date_clean = date_str.strip()
    if not time_str or time_str.strip() == "":
        try:
            return datetime.fromisoformat(date_clean).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    time_clean = time_str.strip()
    # Check if time_clean is already full iso
    full_ts = _timestamp(time_clean)
    if full_ts is not None and full_ts.year > 2000:
        return full_ts
    # Handle MM:SS or MM:SS.s format from spreadsheet export
    if ":" in time_clean:
        parts = time_clean.split(":")
        if len(parts) == 2:
            try:
                # Format into date + 00:MM:SS.s
                minute = int(parts[0])
                sec_part = parts[1]
                iso_candidate = f"{date_clean}T00:{minute:02d}:{sec_part}"
                return datetime.fromisoformat(iso_candidate).replace(tzinfo=timezone.utc)
            except Exception:
                pass
        elif len(parts) == 3:
            try:
                iso_candidate = f"{date_clean}T{time_clean}"
                return datetime.fromisoformat(iso_candidate).replace(tzinfo=timezone.utc)
            except Exception:
                pass
    try:
        return datetime.fromisoformat(date_clean).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _to_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_int(val: Any) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


class CanonicalIngestor:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.raw_dir = root_dir / "raw"
        self.derived_dir = root_dir / "derived"
        self.parquet_dir = self.derived_dir / "parquet"
        self.manifest_path = self.derived_dir / "derived-manifest.json"

    def ingest_all(self, batch_size: int = 50_000) -> dict[str, Any]:
        """Ingest all execution and wallet CSVs, sort canonically, and partition into Parquet."""
        self.parquet_dir.mkdir(parents=True, exist_ok=True)
        raw_files = sorted(self.raw_dir.glob("aoa-execution-*.csv"))
        wallet_file = self.raw_dir / "aoa-wallet-2018-03-01-2021-12-31.csv"
        if not raw_files or not wallet_file.exists():
            raise FileNotFoundError(f"Missing raw files in {self.raw_dir}")

        start_time = datetime.now(timezone.utc)

        # 1. Ingest Execution, Funding, Settlement
        exec_rows = 0
        funding_rows = 0
        settlement_rows = 0
        input_file_meta = []

        exec_batches: list[dict[str, list[Any]]] = []
        funding_batches: list[dict[str, list[Any]]] = []
        settlement_batches: list[dict[str, list[Any]]] = []

        for csv_path in raw_files:
            file_sha = sha256_file(csv_path)
            file_rows = 0
            with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row_idx, row in enumerate(reader, start=1):
                    file_rows += 1
                    exectype = (row.get("exectype") or "").strip()
                    transact_ts = _timestamp(row.get("transacttime"))
                    envelope_ts = _timestamp(row.get("timestamp")) or transact_ts
                    if not transact_ts:
                        transact_ts = envelope_ts
                    if not transact_ts:
                        continue

                    year = transact_ts.year
                    month = transact_ts.month
                    symbol = (row.get("symbol") or "UNKNOWN").strip()

                    if exectype == "Trade":
                        exec_rows += 1
                        rec = {
                            "dataset_id": EXTERNAL_BITMEX_DATASET_ID,
                            "execution_id": (row.get("execid") or "").strip(),
                            "order_id": (row.get("orderid") or "").strip(),
                            "trade_match_id": (row.get("trdmatchid") or "").strip(),
                            "transact_time": transact_ts,
                            "timestamp": envelope_ts,
                            "symbol": symbol,
                            "side": (row.get("side") or "").strip(),
                            "price": _to_float(row.get("lastpx") or row.get("price")),
                            "quantity": _to_float(row.get("lastqty")),
                            "execution_type": exectype,
                            "order_type": (row.get("ordtype") or "").strip(),
                            "order_status": (row.get("ordstatus") or "").strip(),
                            "liquidity_indicator": (row.get("lastliquidityind") or "").strip(),
                            "fee": _to_float(row.get("execcomm")),
                            "fee_currency": (row.get("settlcurrency") or "").strip(),
                            "exec_cost": _to_float(row.get("execcost")),
                            "home_notional": _to_float(row.get("homenotional")),
                            "foreign_notional": _to_float(row.get("foreignnotional")),
                            "order_qty": _to_float(row.get("orderqty")),
                            "leaves_qty": _to_float(row.get("leavesqty")),
                            "cum_qty": _to_float(row.get("cumqty")),
                            "avg_px": _to_float(row.get("avgpx")),
                            "source_file": csv_path.name,
                            "source_row_number": row_idx,
                            "source_sha256": file_sha,
                            "ingestion_version": INGESTION_VERSION,
                            "year": year,
                            "month": month,
                        }
                        self._add_to_batch(exec_batches, rec, batch_size)
                    elif exectype == "Funding":
                        funding_rows += 1
                        rec = {
                            "dataset_id": EXTERNAL_BITMEX_DATASET_ID,
                            "execution_id": (row.get("execid") or "").strip(),
                            "transact_time": transact_ts,
                            "timestamp": envelope_ts,
                            "symbol": symbol,
                            "fee": _to_float(row.get("execcomm")),
                            "fee_currency": (row.get("settlcurrency") or "").strip(),
                            "exec_cost": _to_float(row.get("execcost")),
                            "home_notional": _to_float(row.get("homenotional")),
                            "foreign_notional": _to_float(row.get("foreignnotional")),
                            "source_file": csv_path.name,
                            "source_row_number": row_idx,
                            "source_sha256": file_sha,
                            "ingestion_version": INGESTION_VERSION,
                            "year": year,
                            "month": month,
                        }
                        self._add_to_batch(funding_batches, rec, batch_size)
                    elif exectype == "Settlement":
                        settlement_rows += 1
                        rec = {
                            "dataset_id": EXTERNAL_BITMEX_DATASET_ID,
                            "execution_id": (row.get("execid") or "").strip(),
                            "transact_time": transact_ts,
                            "timestamp": envelope_ts,
                            "symbol": symbol,
                            "price": _to_float(row.get("lastpx") or row.get("price")),
                            "quantity": _to_float(row.get("lastqty")),
                            "fee": _to_float(row.get("execcomm")),
                            "fee_currency": (row.get("settlcurrency") or "").strip(),
                            "source_file": csv_path.name,
                            "source_row_number": row_idx,
                            "source_sha256": file_sha,
                            "ingestion_version": INGESTION_VERSION,
                            "year": year,
                            "month": month,
                        }
                        self._add_to_batch(settlement_batches, rec, batch_size)

            input_file_meta.append({
                "filename": csv_path.name,
                "sha256": file_sha,
                "size_bytes": csv_path.stat().st_size,
                "row_count": file_rows,
            })

        # 2. Write Execution Parquet dataset
        exec_pq_dir = self.parquet_dir / "execution"
        self._flush_and_write(exec_batches, EXECUTION_SCHEMA, exec_pq_dir, ["year", "month", "symbol"])

        # 3. Write Funding Parquet dataset
        funding_pq_dir = self.parquet_dir / "funding"
        self._flush_and_write(funding_batches, FUNDING_SCHEMA, funding_pq_dir, ["year", "month"])

        # 4. Write Settlement Parquet dataset
        settlement_pq_dir = self.parquet_dir / "settlement"
        self._flush_and_write(settlement_batches, SETTLEMENT_SCHEMA, settlement_pq_dir, ["year"])

        # 5. Ingest and Write Wallet
        wallet_batches: list[dict[str, list[Any]]] = []
        wallet_rows = 0
        wallet_blank_rows = 0
        wallet_sha = sha256_file(wallet_file)
        with wallet_file.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row_idx, row in enumerate(reader, start=1):
                transacttype = (row.get("transacttype") or "").strip()
                if not transacttype:
                    wallet_blank_rows += 1
                    continue
                date_str = (row.get("date") or "").strip()
                time_str = (row.get("transacttime") or row.get("timestamp") or "").strip()
                ts = _parse_wallet_datetime(date_str, time_str)
                if not ts:
                    continue
                wallet_rows += 1
                amt_sat = _to_int(row.get("amount")) or 0
                bal_sat = _to_int(row.get("walletbalance")) or 0
                rec = {
                    "dataset_id": EXTERNAL_BITMEX_DATASET_ID,
                    "transact_id": (row.get("transactid") or "").strip(),
                    "date": date_str,
                    "transact_time": ts,
                    "timestamp": ts,
                    "event_type": transacttype,
                    "amount_satoshi": amt_sat,
                    "amount_xbt": amt_sat / 1e8,
                    "currency": (row.get("currency") or "XBt").strip(),
                    "wallet_balance_satoshi": bal_sat,
                    "wallet_balance_xbt": bal_sat / 1e8,
                    "address": (row.get("address") or "").strip(),
                    "network": (row.get("network") or "").strip(),
                    "tx": (row.get("tx") or "").strip(),
                    "source_file": wallet_file.name,
                    "source_row_number": row_idx,
                    "source_sha256": wallet_sha,
                    "ingestion_version": INGESTION_VERSION,
                    "year": ts.year,
                    "month": ts.month,
                }
                self._add_to_batch(wallet_batches, rec, batch_size)

        input_file_meta.append({
            "filename": wallet_file.name,
            "sha256": wallet_sha,
            "size_bytes": wallet_file.stat().st_size,
            "row_count": wallet_rows + wallet_blank_rows,
            "valid_rows": wallet_rows,
            "blank_rows": wallet_blank_rows,
        })

        wallet_pq_dir = self.parquet_dir / "wallet"
        self._flush_and_write(wallet_batches, WALLET_SCHEMA, wallet_pq_dir, ["year", "month"])

        end_time = datetime.now(timezone.utc)
        elapsed_seconds = (end_time - start_time).total_seconds()

        # Compute derived statistics
        parquet_files = list(self.parquet_dir.rglob("*.parquet"))
        total_parquet_bytes = sum(p.stat().st_size for p in parquet_files)

        manifest = {
            "dataset_id": EXTERNAL_BITMEX_DATASET_ID,
            "schema_version": "1",
            "ingestion_version": INGESTION_VERSION,
            "ingested_at_utc": start_time.isoformat(),
            "ingestion_duration_seconds": elapsed_seconds,
            "input_files": input_file_meta,
            "canonical_counts": {
                "execution_trades": exec_rows,
                "funding_events": funding_rows,
                "settlement_events": settlement_rows,
                "wallet_events": wallet_rows,
                "wallet_blank_rows": wallet_blank_rows,
                "total_execution_rows": exec_rows + funding_rows + settlement_rows,
            },
            "derived_storage": {
                "format": "parquet",
                "compression": "zstd",
                "parquet_files_count": len(parquet_files),
                "total_bytes": total_parquet_bytes,
                "execution_dir": str(exec_pq_dir.relative_to(self.root_dir)),
                "funding_dir": str(funding_pq_dir.relative_to(self.root_dir)),
                "settlement_dir": str(settlement_pq_dir.relative_to(self.root_dir)),
                "wallet_dir": str(wallet_pq_dir.relative_to(self.root_dir)),
            },
        }

        self.manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return manifest

    def _add_to_batch(
        self,
        batches: list[dict[str, list[Any]]],
        record: dict[str, Any],
        max_batch_size: int,
    ) -> None:
        if not batches or len(batches[-1]["dataset_id"]) >= max_batch_size:
            batches.append({k: [] for k in record.keys()})
        current = batches[-1]
        for k, v in record.items():
            current[k].append(v)

    def _flush_and_write(
        self,
        batches: list[dict[str, list[Any]]],
        schema: pa.Schema,
        output_dir: Path,
        partition_cols: list[str],
    ) -> None:
        if not batches:
            return
        output_dir.mkdir(parents=True, exist_ok=True)
        # Convert each batch to pyarrow table, sort canonically, and write
        for batch_dict in batches:
            if not batch_dict["dataset_id"]:
                continue
            table = pa.Table.from_pydict(batch_dict, schema=schema)
            # Canonical sort by transact_time, timestamp
            sort_keys = [("transact_time", "ascending"), ("timestamp", "ascending")]
            if "order_id" in table.column_names:
                sort_keys.append(("order_id", "ascending"))
            if "execution_id" in table.column_names:
                sort_keys.append(("execution_id", "ascending"))
            sort_fn = getattr(pc, "sort_indices")
            sorted_indices = sort_fn(table, sort_keys=sort_keys)
            sorted_table = table.take(sorted_indices)

            pq.write_to_dataset(
                sorted_table,
                root_path=str(output_dir),
                partition_cols=partition_cols,
                compression="zstd",
                existing_data_behavior="overwrite_or_ignore",
            )
        batches.clear()
