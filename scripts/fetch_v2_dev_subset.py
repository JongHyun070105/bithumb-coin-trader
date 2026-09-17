"""Fetch minimal V2 DEV subset from S3 for retrospective research.

Downloads Block D1 (2026-09-12_11 through 2026-09-12_16) for:
- Bithumb: KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL (orderbook, trade)
- Binance: btcusdt, ethusdt, xrpusdt, solusdt (orderbook, trade)
- Upbit: KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL (orderbook, trade)

Total size is ~35 MB compressed. Preserves disk space and satisfies research requirements.
"""

from pathlib import Path
import boto3

PROFILE = "bitcoin-trader-bootstrap"
REGION = "ap-northeast-2"
BUCKET = "bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433"
PREFIX = "market-data/temporary/aws-validation-30h-20260912-6576f63/"

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEST = ROOT / "data" / "research" / "v2"

# Block D1 hours
DEV_HOURS = [f"2026-09-12_{h:02d}" for h in range(11, 17)]
MARKETS = ["btc", "eth", "xrp", "sol"]

def main():
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    s3 = session.client("s3")

    downloaded = 0
    total_bytes = 0

    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET, Prefix=PREFIX)

    for page in pages:
        for item in page.get("Contents", []):
            key = item["Key"]
            size = item["Size"]

            # Filter for DEV hours
            if not any(h in key for h in DEV_HOURS):
                continue

            # Filter for markets
            key_lower = key.lower()
            if not any(m in key_lower for m in MARKETS):
                continue

            # Skip tickers (only orderbook + trade needed for microstructure)
            if "/ticker/" in key:
                continue

            rel_path = key[len(PREFIX):]
            dest_file = LOCAL_DEST / rel_path

            if dest_file.exists() and dest_file.stat().st_size == size:
                continue

            dest_file.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(BUCKET, key, str(dest_file))
            downloaded += 1
            total_bytes += size
            print(f"Downloaded: {rel_path} ({size/1024:.1f} KB)")

    print(f"\nDone! Downloaded {downloaded} files ({total_bytes/1024/1024:.1f} MB) to {LOCAL_DEST}")

if __name__ == "__main__":
    main()
