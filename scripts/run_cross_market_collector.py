"""Run Enterprise Multi-Exchange Microstructure Collector Daemon."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import sys

from bithumb_coin_trader.cross_market_collector import MultiExchangeMicrostructureCollector
from bithumb_coin_trader.dynamic_universe import TOP_UNIVERSE_CANDIDATES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def _run(args: argparse.Namespace) -> None:
    bithumb_mkts = list(TOP_UNIVERSE_CANDIDATES[: args.bithumb_markets])
    binance_syms = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
    upbit_mkts = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"]
    config = _load_runtime_config(args.config_file, args.config_fingerprint)
    _validate_runtime_config(config, args, bithumb_mkts, binance_syms, upbit_mkts)

    print("=" * 80)
    print("  LAUNCHING MULTI-EXCHANGE MICROSTRUCTURE COLLECTOR DAEMON (V9)")
    print(f"  - Bithumb KRW Markets ({len(bithumb_mkts)}): {bithumb_mkts[:5]} ...")
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
    )

    try:
        await collector.run_collector(max_duration_seconds=args.duration)
    finally:
        print("Flushing final manifests...")
        mfs = collector.generate_all_manifests()
        print(f"Generated {len(mfs)} partition manifests.")


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
        "sealed environment": args.environment_id not in {"", "UNKNOWN", "NOT-SEALED"},
        "sealed epoch": args.collector_epoch not in {"", "UNKNOWN", "NOT-SEALED"},
        "sealed run ID": args.run_id not in {"", "UNKNOWN", "NOT-SEALED"},
        "duration": config.get("duration_seconds") == args.duration and args.duration > 0,
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Exchange Microstructure Collector Daemon")
    parser.add_argument("--bithumb-markets", type=int, default=20, help="Number of Bithumb KRW markets (default: 20)")
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--config-file", type=Path, required=True)
    parser.add_argument("--storage-base-dir", type=Path, required=True)
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--collector-epoch", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config-fingerprint", required=True)
    parser.add_argument("--runtime-commit", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nCollector gracefully stopped.")


if __name__ == "__main__":
    main()
