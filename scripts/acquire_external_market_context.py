"""Resumably acquire public hourly BitMEX and Binance market history.

Raw downloads stay below .external-research-data and are never rewritten. Each
artifact is checksum-bound in market-context-source-manifest.json.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile


START = datetime(2018, 3, 1, tzinfo=timezone.utc)
END = datetime(2022, 1, 1, tzinfo=timezone.utc)
BINANCE_SYMBOLS = ("BTCUSDT", "ETHUSDT")
BITMEX_SYMBOLS = ("XBTUSD", "ETHUSD")
USER_AGENT = "bithumb-coin-trader-public-research/1.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, *, timeout: int = 60) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def write_new_or_match(path: Path, content: bytes, expected_hash: str | None = None) -> str:
    """Create atomically; if present, preserve and require byte-identical content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        actual_hash = sha256_file(path)
        if expected_hash and actual_hash != expected_hash:
            raise ValueError(f"Existing raw artifact checksum differs from source: {path}")
        if hashlib.sha256(content).hexdigest() != actual_hash:
            raise ValueError(f"Refusing to overwrite changed raw artifact: {path}")
        return actual_hash
    part_path = path.with_name(path.name + ".part")
    if part_path.exists():
        raise FileExistsError(f"Partial artifact exists; inspect and preserve it before resuming: {part_path}")
    part_path.write_bytes(content)
    actual_hash = sha256_file(part_path)
    if expected_hash and actual_hash != expected_hash:
        part_path.unlink()
        raise ValueError(f"Downloaded checksum mismatch for {path}: {actual_hash} != {expected_hash}")
    part_path.replace(path)
    return actual_hash


def month_starts() -> list[datetime]:
    current = START
    result: list[datetime] = []
    while current < END:
        result.append(current)
        year = current.year + (1 if current.month == 12 else 0)
        month = 1 if current.month == 12 else current.month + 1
        current = datetime(year, month, 1, tzinfo=timezone.utc)
    return result


def month_code(month: datetime) -> str:
    return month.strftime("%Y-%m")


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "dataset_id": "external-bitmex-market-context-2018-2021",
            "period_utc": {"start_inclusive": START.isoformat(), "end_exclusive": END.isoformat()},
            "frequency": "1h",
            "download_started_or_resumed_at_utc": datetime.now(timezone.utc).isoformat(),
            "artifacts": [],
            "limitations": [
                "Public venue market data does not establish dataset publisher identity or account ownership.",
                "OHLCV cannot recover historical order book depth, queue position, or unfilled orders.",
                "Binance BTCUSDT/ETHUSDT is an independent spot price cross-check, not the BitMEX execution venue.",
                "Market data is descriptive context only and cannot establish transferable alpha.",
            ],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["last_updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def artifact_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["path"]: item for item in manifest["artifacts"]}


def record_artifact(manifest: dict[str, Any], item: dict[str, Any]) -> None:
    by_path = artifact_index(manifest)
    previous = by_path.get(item["path"])
    if previous and previous["sha256"] != item["sha256"]:
        raise ValueError(f"Manifest already binds a different checksum to {item['path']}")
    by_path[item["path"]] = item
    manifest["artifacts"] = [by_path[key] for key in sorted(by_path)]


def acquire_binance(root: Path, manifest: dict[str, Any], manifest_path: Path) -> None:
    for symbol in BINANCE_SYMBOLS:
        for month in month_starts():
            code = month_code(month)
            name = f"{symbol}-1h-{code}.zip"
            relative = Path("binance") / symbol / "1h" / name
            path = root / relative
            base = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1h/{name}"
            checksum_bytes = fetch(base + ".CHECKSUM")
            checksum_match = re.fullmatch(rb"\s*([0-9a-fA-F]{64})\s+" + re.escape(name.encode()) + rb"\s*", checksum_bytes)
            if not checksum_match:
                raise ValueError(f"Unexpected Binance checksum format: {checksum_bytes[:200]!r}")
            expected = checksum_match.group(1).decode().lower()
            checksum_relative = relative.with_name(relative.name + ".CHECKSUM")
            checksum_path = root / checksum_relative
            checksum_hash = write_new_or_match(checksum_path, checksum_bytes)
            if not path.exists():
                archive_bytes = fetch(base)
                actual = hashlib.sha256(archive_bytes).hexdigest()
                if actual != expected:
                    raise ValueError(f"Binance SHA-256 mismatch for {name}: {actual} != {expected}")
                write_new_or_match(path, archive_bytes, expected)
            actual = sha256_file(path)
            if actual != expected:
                raise ValueError(f"Preserved Binance archive no longer matches publisher checksum: {path}")
            with zipfile.ZipFile(path) as archive:
                bad_member = archive.testzip()
                if bad_member:
                    raise ValueError(f"Corrupt Binance archive {path}: {bad_member}")
            record_artifact(manifest, {
                "provider": "Binance Public Data",
                "endpoint_or_dataset": base,
                "instrument": symbol,
                "frequency": "1h spot klines",
                "coverage_claim": code,
                "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
                "path": relative.as_posix(),
                "sha256": actual,
                "publisher_sha256": expected,
                "checksum_manifest_path": checksum_relative.as_posix(),
                "checksum_manifest_sha256": checksum_hash,
                "known_limitations": [
                    "Exchange-published archives can be corrected later; a conflicting existing artifact is preserved and causes a hard error.",
                    "Spot volume is denominated in base asset and is not directly comparable to BitMEX contracts.",
                ],
            })
            save_manifest(manifest_path, manifest)
            print(f"BINANCE {symbol} {code} SHA256={actual}")


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def acquire_bitmex_series(
    root: Path,
    manifest: dict[str, Any],
    manifest_path: Path,
    symbol: str,
    endpoint_kind: str,
) -> None:
    if endpoint_kind == "trade_bucketed":
        endpoint = "https://www.bitmex.com/api/v1/trade/bucketed"
        params_base = {"binSize": "1h", "symbol": symbol, "reverse": "false"}
        cursor = START
        frequency = "1h OHLCV/trade-count buckets"
        end_limit = END
    elif endpoint_kind == "funding":
        endpoint = "https://www.bitmex.com/api/v1/funding"
        params_base = {"symbol": symbol, "reverse": "false"}
        cursor = START
        frequency = "published funding events (typically 8h)"
        end_limit = END
    else:
        raise ValueError(endpoint_kind)

    directory = root / "bitmex" / symbol / endpoint_kind
    directory.mkdir(parents=True, exist_ok=True)
    page_paths = sorted(directory.glob("part-*.json"))
    expected_index = 0
    for page_path in page_paths:
        if page_path.name != f"part-{expected_index:04d}.json":
            raise ValueError(f"Non-contiguous preserved BitMEX pages under {directory}")
        page = json.loads(page_path.read_text(encoding="utf-8"))
        records = page["records"]
        if not records:
            raise ValueError(f"Empty preserved BitMEX page: {page_path}")
        for record in records:
            if record.get("symbol") != symbol:
                raise ValueError(f"Symbol mismatch in preserved page {page_path}")
        last_time = parse_timestamp(records[-1]["timestamp"])
        from datetime import timedelta
        cursor = last_time + timedelta(milliseconds=1)
        expected_index += 1

    while cursor < end_limit:
        params = dict(params_base)
        params["count"] = "500"
        params["startTime"] = cursor.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        request_url = endpoint + "?" + urlencode(params)
        try:
            response_bytes = fetch(request_url)
        except HTTPError as exc:
            raise RuntimeError(f"BitMEX {endpoint_kind} HTTP {exc.code} at {request_url}") from exc
        except URLError as exc:
            raise RuntimeError(f"BitMEX {endpoint_kind} request failed at {request_url}: {exc}") from exc
        records = json.loads(response_bytes)
        if not isinstance(records, list):
            raise ValueError(f"Unexpected BitMEX response shape: {type(records).__name__}")
        records = [row for row in records if parse_timestamp(row["timestamp"]) < end_limit]
        if not records:
            break
        times = [parse_timestamp(row["timestamp"]) for row in records]
        if times != sorted(times) or len(times) != len(set(times)):
            raise ValueError(f"BitMEX page contains non-monotone or duplicate timestamps for {symbol}")
        if times[0] < cursor:
            raise ValueError(f"BitMEX API returned data before requested cursor for {symbol}")
        if any(row.get("symbol") != symbol for row in records):
            raise ValueError(f"BitMEX API returned another instrument for {symbol}")

        relative = Path("bitmex") / symbol / endpoint_kind / f"part-{expected_index:04d}.json"
        path = root / relative
        artifact = {
            "source": "BitMEX Public REST API",
            "endpoint": endpoint,
            "query": params,
            "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
            "records": records,
        }
        content = (json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n").encode()
        digest = write_new_or_match(path, content)
        record_artifact(manifest, {
            "provider": "BitMEX Public REST API",
            "endpoint_or_dataset": request_url,
            "instrument": symbol,
            "frequency": frequency,
            "coverage_claim": {"start": times[0].isoformat(), "end": times[-1].isoformat(), "rows": len(records)},
            "downloaded_at_utc": artifact["downloaded_at_utc"],
            "path": relative.as_posix(),
            "sha256": digest,
            "known_limitations": [
                "Public endpoint output may not include empty intervals; gaps must be measured from timestamps.",
                "API payload is preserved page-by-page; a later provider correction is not silently substituted.",
            ],
        })
        save_manifest(manifest_path, manifest)
        print(f"BITMEX {endpoint_kind} {symbol} page={expected_index} rows={len(records)} through={times[-1].isoformat()}")
        expected_index += 1
        if len(records) < 500:
            break
        from datetime import timedelta
        cursor = times[-1] + timedelta(milliseconds=1)
        if cursor <= times[-1]:
            raise AssertionError("BitMEX pagination cursor failed to advance")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=Path(".external-research-data/external-bitmex-market-context-2018-2021"))
    parser.add_argument("--source", choices=("all", "binance", "bitmex"), default="all")
    args = parser.parse_args()
    root = args.output_root
    manifest_path = root / "market-context-source-manifest.json"
    manifest = load_manifest(manifest_path)

    if args.source in ("all", "binance"):
        acquire_binance(root, manifest, manifest_path)
    if args.source in ("all", "bitmex"):
        for symbol in BITMEX_SYMBOLS:
            acquire_bitmex_series(root, manifest, manifest_path, symbol, "trade_bucketed")
            acquire_bitmex_series(root, manifest, manifest_path, symbol, "funding")
    save_manifest(manifest_path, manifest)
    print(f"MANIFEST={manifest_path}")
    print(f"ARTIFACTS={len(manifest['artifacts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
