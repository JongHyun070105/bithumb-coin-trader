"""Generate complete recursive S3 inventory for V4 prefix.

Prefix: market-data/temporary/aws-validation-30h-20260915-v4/
Output: research-artifacts/v4-authoritative/source/V4_S3_INVENTORY.json
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import boto3

PROFILE = "bitcoin-trader-provisioner"
REGION = "ap-northeast-2"
BUCKET = "bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433"
PREFIX = "market-data/temporary/aws-validation-30h-20260915-v4/"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "research-artifacts" / "v4-authoritative" / "source" / "V4_S3_INVENTORY.json"

def main():
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    s3 = session.client("s3")

    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET, Prefix=PREFIX)

    objects = []
    by_hour = defaultdict(list)
    by_exchange = defaultdict(list)
    by_feed = defaultdict(list)
    by_artifact_kind = defaultdict(list)
    total_bytes = 0

    latest_modified_utc = None

    for page in pages:
        for item in page.get("Contents", []):
            key = item["Key"]
            size = item["Size"]
            last_mod = item["LastModified"].isoformat()
            etag = item["ETag"].strip('"')
            total_bytes += size

            if latest_modified_utc is None or last_mod > latest_modified_utc:
                latest_modified_utc = last_mod

            # Classify kind
            rel_path = key[len(PREFIX):]
            parts = rel_path.split("/")

            # Determine artifact kind
            if parts[0] == "coverage":
                kind = "COVERAGE_EVIDENCE"
                hour = parts[1] if len(parts) > 1 else "unknown"
                exchange = parts[2] if len(parts) > 2 else "unknown"
                feed = parts[3] if len(parts) > 3 else "unknown"
                fname = parts[4] if len(parts) > 4 else ""
                market = fname.split(".")[0] if fname else "unknown"
            elif parts[0] == "raw":
                kind = "RAW_DATA"
                hour = parts[1] if len(parts) > 1 else "unknown"
                exchange = parts[2] if len(parts) > 2 else "unknown"
                feed = parts[3] if len(parts) > 3 else "unknown"
                market = "unknown"
            elif "manifest" in key:
                kind = "MANIFEST"
                hour, exchange, feed, market = "unknown", "unknown", "unknown", "unknown"
            elif "heartbeat" in key or "liveness" in key:
                kind = "HEARTBEAT"
                hour, exchange, feed, market = "unknown", "unknown", "unknown", "unknown"
            elif "receipt" in key:
                kind = "RECEIPT"
                hour, exchange, feed, market = "unknown", "unknown", "unknown", "unknown"
            elif "final" in key:
                kind = "FINALIZATION"
                hour, exchange, feed, market = "unknown", "unknown", "unknown", "unknown"
            elif "wal" in key or "transaction" in key:
                kind = "WAL_TRANSACTION"
                hour, exchange, feed, market = "unknown", "unknown", "unknown", "unknown"
            else:
                kind = "OTHER"
                hour, exchange, feed, market = "unknown", "unknown", "unknown", "unknown"

            obj_info = {
                "key": key,
                "relative_path": rel_path,
                "size_bytes": size,
                "last_modified_utc": last_mod,
                "etag": etag,
                "artifact_kind": kind,
                "hour": hour,
                "exchange": exchange,
                "feed": feed,
                "market": market,
            }
            objects.append(obj_info)
            by_hour[hour].append(key)
            by_exchange[exchange].append(key)
            by_feed[feed].append(key)
            by_artifact_kind[kind].append(key)

    inventory = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "s3_bucket": BUCKET,
        "s3_prefix": PREFIX,
        "total_objects": len(objects),
        "total_bytes": total_bytes,
        "latest_modified_utc": latest_modified_utc,
        "summary_by_artifact_kind": {k: len(v) for k, v in by_artifact_kind.items()},
        "summary_by_hour": {k: len(v) for k, v in by_hour.items()},
        "summary_by_exchange": {k: len(v) for k, v in by_exchange.items()},
        "summary_by_feed": {k: len(v) for k, v in by_feed.items()},
        "artifact_kinds": {
            "RAW_DATA_count": len(by_artifact_kind.get("RAW_DATA", [])),
            "COVERAGE_EVIDENCE_count": len(by_artifact_kind.get("COVERAGE_EVIDENCE", [])),
            "RECEIPT_count": len(by_artifact_kind.get("RECEIPT", [])),
            "MANIFEST_count": len(by_artifact_kind.get("MANIFEST", [])),
            "HEARTBEAT_count": len(by_artifact_kind.get("HEARTBEAT", [])),
            "FINALIZATION_count": len(by_artifact_kind.get("FINALIZATION", [])),
            "WAL_TRANSACTION_count": len(by_artifact_kind.get("WAL_TRANSACTION", [])),
            "OTHER_count": len(by_artifact_kind.get("OTHER", [])),
        },
        "objects": objects,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(inventory, indent=2))
    print(f"Wrote {len(objects)} objects ({total_bytes} bytes) to {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
