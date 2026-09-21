"""Run Enterprise Multi-Exchange Microstructure Collector Daemon."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from collections.abc import Sequence

from bithumb_coin_trader.cross_market_collector import MultiExchangeMicrostructureCollector
from bithumb_coin_trader.dynamic_universe import TOP_UNIVERSE_CANDIDATES
from bithumb_coin_trader.incremental_finalizer import FinalizationState, FinalizationSummary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def _run(args: argparse.Namespace) -> None:
    bithumb_mkts: list[str] = list(TOP_UNIVERSE_CANDIDATES[: args.bithumb_markets])
    binance_syms = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
    upbit_mkts = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"]
    config = _load_runtime_config(args.config_file, args.config_fingerprint)
    _validate_runtime_config(config, args, bithumb_mkts, binance_syms, upbit_mkts)
    redundancy = config.get("bithumb_redundancy")
    bithumb_connection_count = (
        int(redundancy["physical_connections"]) if isinstance(redundancy, dict) else 1
    )

    print("=" * 80)
    print("  LAUNCHING MULTI-EXCHANGE MICROSTRUCTURE COLLECTOR DAEMON (V9)")
    print(f"  - Bithumb KRW Markets ({len(bithumb_mkts)}): {bithumb_mkts[:5]} ...")
    print(f"  - Bithumb physical connections: {bithumb_connection_count}")
    print(f"  - Binance Global Benchmark ({len(binance_syms)}): {binance_syms}")
    print(f"  - Upbit Domestic Benchmark ({len(upbit_mkts)}): {upbit_mkts}")
    print(f"  - Mode: {'INDEFINITE (DAEMON)' if args.duration is None else f'{args.duration} SECONDS'}")
    print(f"  - Environment: {args.environment_id}")
    print(f"  - Collector epoch: {args.collector_epoch}")
    print(f"  - Collector run ID: {args.run_id}")
    print(f"  - Config fingerprint: {args.config_fingerprint}")
    print("=" * 80)

    collector = MultiExchangeMicrostructureCollector(
        bithumb_markets=bithumb_mkts,
        binance_symbols=binance_syms,
        upbit_markets=upbit_mkts,
        storage_base_dir=args.storage_base_dir,
        environment_id=args.environment_id,
        collector_epoch=args.collector_epoch,
        collector_run_id=args.run_id,
        collector_config_fingerprint=args.config_fingerprint,
        collector_git_commit=args.runtime_commit,
        bithumb_connection_count=bithumb_connection_count,
    )

    collector_error: BaseException | None = None
    if args.lifecycle_status_path is not None:
        _write_lifecycle_status(
            args.lifecycle_status_path,
            run_id=args.run_id,
            phase="COLLECTING",
            final_manifest_flush_observed=False,
            manifest_count=0,
            error_type=None,
        )
    try:
        await collector.run_collector(max_duration_seconds=args.duration_to_run)
    except BaseException as error:
        collector_error = error
        raise
    finally:
        print("Flushing final manifests with IncrementalManifestFinalizer...")
        if args.lifecycle_status_path is not None:
            _write_lifecycle_status(
                args.lifecycle_status_path,
                run_id=args.run_id,
                phase="FINALIZING",
                final_manifest_flush_observed=False,
                manifest_count=0,
                error_type=type(collector_error).__name__ if collector_error is not None else None,
            )
        summary: FinalizationSummary | None = None
        flush_observed = False
        flush_error: BaseException | None = None
        try:
            summary = collector.finalize_all()
            print(
                f"Finalization summary: state={summary.state}, "
                f"reused={summary.reused_count}, recomputed={summary.recomputed_count}, "
                f"pending={summary.pending_count}, failed={summary.failed_count}"
            )
            if (
                summary.state == "COMPLETE"
                and summary.pending_count == 0
                and summary.failed_count == 0
                and collector_error is None
            ):
                flush_observed = True
        except BaseException as error:
            flush_error = error
            raise
        finally:
            if args.lifecycle_status_path is not None:
                reused_count = summary.reused_count if summary is not None else 0
                generated_count = summary.recomputed_count if summary is not None else 0
                total_manifests = reused_count + generated_count
                hist_files = summary.historical_raw_files_opened if summary is not None else 0
                hist_bytes = summary.historical_raw_bytes_read if summary is not None else 0
                curr_files = summary.recomputed_count if summary is not None else 0
                curr_bytes = 0
                if summary is not None and collector.finalizer_store is not None:
                    try:
                        for entry_file in collector.finalizer_store.entries_dir.glob("*.json"):
                            entry_dict = json.loads(entry_file.read_text(encoding="utf-8"))
                            if entry_dict.get("state") == FinalizationState.RECOMPUTED.value:
                                curr_bytes += int(entry_dict.get("source_size") or 0)
                    except Exception:
                        pass

                _write_lifecycle_status(
                    args.lifecycle_status_path,
                    run_id=args.run_id,
                    phase="COMPLETE" if flush_observed else "FINALIZING",
                    final_manifest_flush_observed=flush_observed,
                    manifest_count=total_manifests,
                    historical_raw_files_opened=hist_files,
                    historical_raw_bytes_read=hist_bytes,
                    current_raw_files_opened=curr_files,
                    current_raw_bytes_read=curr_bytes,
                    reused_manifest_count=reused_count,
                    generated_manifest_count=generated_count,
                    error_type=(
                        type(flush_error).__name__
                        if flush_error is not None
                        else type(collector_error).__name__ if collector_error is not None else None
                    ),
                )


def _write_lifecycle_status(
    path: Path,
    *,
    run_id: str,
    phase: str,
    final_manifest_flush_observed: bool,
    manifest_count: int,
    error_type: str | None,
    historical_raw_files_opened: int = 0,
    historical_raw_bytes_read: int = 0,
    current_raw_files_opened: int = 0,
    current_raw_bytes_read: int = 0,
    reused_manifest_count: int = 0,
    generated_manifest_count: int = 0,
) -> None:
    if phase not in {"COLLECTING", "FINALIZING", "COMPLETE"}:
        raise ValueError("lifecycle phase must be COLLECTING, FINALIZING, or COMPLETE")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "collector_run_id": run_id,
        "phase": phase,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "process_id": os.getpid(),
        "final_manifest_flush_observed": final_manifest_flush_observed,
        "manifest_count": manifest_count,
        "historical_raw_files_opened": historical_raw_files_opened,
        "historical_raw_bytes_read": historical_raw_bytes_read,
        "current_raw_files_opened": current_raw_files_opened,
        "current_raw_bytes_read": current_raw_bytes_read,
        "reused_manifest_count": reused_manifest_count,
        "generated_manifest_count": generated_manifest_count,
        "error_type": error_type,
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def canonical_config_fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_runtime_config(path: Path, expected_fingerprint: str) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("runtime config must be a schema-version 1 JSON object")
    actual_fingerprint = canonical_config_fingerprint(payload)
    if actual_fingerprint != expected_fingerprint:
        raise ValueError("runtime config fingerprint mismatch")
    return payload


def _render_epoch_template(value: object, collector_epoch: str, field: str) -> str:
    if not isinstance(value, str) or value.count("{collector_epoch}") != 1:
        raise ValueError(f"{field} must contain exactly one {{collector_epoch}} placeholder")
    rendered = value.replace("{collector_epoch}", collector_epoch)
    if "{" in rendered or "}" in rendered:
        raise ValueError(f"{field} contains an unsupported template placeholder")
    return rendered


def _validate_runtime_config(
    config: dict[str, object],
    args: argparse.Namespace,
    bithumb_markets: list[str],
    binance_symbols: list[str],
    upbit_markets: list[str],
) -> None:
    feeds = config.get("feeds")
    paths = config.get("paths")
    archive = config.get("archive")
    metrics = config.get("metrics")
    disk = config.get("disk_threshold_percent")
    redundancy = config.get("bithumb_redundancy")
    if not all(isinstance(value, dict) for value in (feeds, paths, archive, metrics, disk)):
        raise ValueError("runtime config sections are incomplete")
    assert isinstance(feeds, dict)
    assert isinstance(paths, dict)
    assert isinstance(archive, dict)
    assert isinstance(metrics, dict)
    assert isinstance(disk, dict)
    expected_raw_root = Path(
        _render_epoch_template(paths.get("raw_root_template"), args.collector_epoch, "raw_root_template")
    )
    expected_manifest_root = Path(
        _render_epoch_template(
            paths.get("manifest_root_template"), args.collector_epoch, "manifest_root_template"
        )
    )
    expected_compressed_root = Path(
        _render_epoch_template(
            paths.get("compressed_root_template"), args.collector_epoch, "compressed_root_template"
        )
    )
    expected_receipt_root = Path(
        _render_epoch_template(
            paths.get("receipt_root_template"), args.collector_epoch, "receipt_root_template"
        )
    )
    expected_metrics_path = Path(
        _render_epoch_template(
            paths.get("metrics_path_template"), args.collector_epoch, "metrics_path_template"
        )
    )
    expected_archive_prefix = _render_epoch_template(
        archive.get("temporary_prefix_template"),
        args.collector_epoch,
        "temporary_prefix_template",
    )
    checks = {
        "runtime software commit": config.get("runtime_software_commit") == args.runtime_commit,
        "environment": config.get("environment_id") == args.environment_id,
        "region": config.get("region") == "ap-northeast-2",
        "architecture": config.get("architecture") == "x86_64",
        "raw schema": config.get("raw_schema_version") == 4,
        "clock source": config.get("clock_source") == "Amazon Time Sync Service 169.254.169.123",
        "public data only": config.get("public_data_only") is True,
        "Bithumb redundancy": (
            redundancy is None  # immutable legacy configs retain their one-socket behavior
            or redundancy == {
                "mode": "ACTIVE_ACTIVE",
                "physical_connections": 2,
                "dedup_max_entries": 100000,
                "dedup_retention_seconds": 180,
                "connection_attempt_interval_seconds": 0.25,
                "established_retry_delay_seconds": {"minimum": 0.05, "maximum": 0.20},
            }
        ),
        "sealed environment": args.environment_id not in {"", "UNKNOWN", "NOT-SEALED"},
        "sealed epoch": args.collector_epoch not in {"", "UNKNOWN", "NOT-SEALED"},
        "sealed run ID": args.run_id not in {"", "UNKNOWN", "NOT-SEALED"},
        "duration": (
            config.get("duration_seconds") == 0
            if getattr(args, "qualification_schedule_path", None) is not None
            else config.get("duration_seconds") == getattr(args, "duration", None) and getattr(args, "duration", 0) > 0
        ),
        "raw root": expected_raw_root == args.storage_base_dir,
        "manifest root": expected_manifest_root == args.storage_base_dir.parent / "manifests",
        "compressed root": expected_compressed_root == args.storage_base_dir.parent / "compressed",
        "receipt root": expected_receipt_root == args.storage_base_dir.parent / "archive-receipts",
        "metrics path": expected_metrics_path == args.storage_base_dir.parent / "collector_metrics.json",
        "Bithumb count": feeds.get("bithumb_market_count") == args.bithumb_markets,
        "Bithumb markets": feeds.get("bithumb_markets") == bithumb_markets,
        "Binance symbols": feeds.get("binance_symbols") == binance_symbols,
        "Upbit markets": feeds.get("upbit_markets") == upbit_markets,
        "archive class": archive.get("remote_class") == "temporary",
        "archive prefix": expected_archive_prefix
        == f"market-data/temporary/{args.collector_epoch}",
        "cleanup disabled": archive.get("cleanup_enabled") is False,
        "archive concurrency": archive.get("worker_concurrency") == 1,
        "archive grace": archive.get("grace_seconds") == 600,
        "compression": archive.get("compression") == {"algorithm": "zstd", "level": 1},
        "metric cadence": metrics.get("publish_cadence_seconds") == 60,
        "disk thresholds": disk == {"warning": 70, "high": 80, "critical": 90},
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError("runtime config does not match collector invocation: " + ", ".join(failed))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Multi-Exchange Microstructure Collector Daemon")
    parser.add_argument("--bithumb-markets", type=int, default=20, help="Number of Bithumb KRW markets (default: 20)")
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--config-file", type=Path, required=True)
    parser.add_argument("--storage-base-dir", type=Path, required=True)
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--collector-epoch", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config-fingerprint", required=True)
    parser.add_argument("--runtime-commit", required=True)
    parser.add_argument("--lifecycle-status-path", type=Path)

    parser.add_argument("--required-qualifying-full-hours", type=int)
    parser.add_argument("--maximum-collection-window-seconds", type=int)
    parser.add_argument("--qualification-schedule-path", type=Path)
    args = parser.parse_args(argv)

    if args.qualification_schedule_path is not None:
        if args.duration > 0:
            raise ValueError("cannot specify both duration and V3 schedule")
        from bithumb_coin_trader.qualification_schedule import load_schedule
        import time
        schedule = load_schedule(args.qualification_schedule_path)
        if args.required_qualifying_full_hours is not None:
            if (
                args.required_qualifying_full_hours != 30
                or args.required_qualifying_full_hours != schedule.required_qualifying_full_hours
            ):
                raise ValueError("required_qualifying_full_hours must be 30 and match schedule")
        if args.maximum_collection_window_seconds is not None:
            if (
                args.maximum_collection_window_seconds != 111600
                or args.maximum_collection_window_seconds != schedule.maximum_collection_window_seconds
            ):
                raise ValueError("maximum_collection_window_seconds must be 111600 and match schedule")
        args.duration_to_run = max(0.0, schedule.collection_stop_monotonic - time.monotonic())
    else:
        if args.required_qualifying_full_hours is not None or args.maximum_collection_window_seconds is not None:
            raise ValueError("qualification_schedule_path must be provided when V3 schedule arguments are used")
        if args.duration <= 0:
            raise ValueError("duration must be positive")
        args.duration_to_run = args.duration

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nCollector gracefully stopped.")


if __name__ == "__main__":
    main()
