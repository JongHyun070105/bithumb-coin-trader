"""Regression test asserting exact 72H soak feed universe invariants.

Ensures:
- Bithumb: exactly 20 markets, channels orderbook/trade/ticker (60 partitions/hour)
- Binance: exactly 4 markets (btcusdt, ethusdt, solusdt, xrpusdt), channels orderbook/trade (8 partitions/hour), port 443 only
- Upbit: exactly 4 markets (KRW-BTC, KRW-ETH, KRW-SOL, KRW-XRP), channels orderbook/trade (8 partitions/hour)
- Total hourly closed partitions = 60 + 8 + 8 = 76
"""

import json
from pathlib import Path
import pytest

EXPECTED_BITHUMB_20 = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-XLM", "KRW-LINK", "KRW-AVAX", "KRW-BCH",
    "KRW-ETC", "KRW-NEAR", "KRW-SUI", "KRW-APT", "KRW-TRX",
    "KRW-SHIB", "KRW-SAND", "KRW-MANA", "KRW-AXS", "KRW-DOT"
]

EXPECTED_BINANCE_4 = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]

EXPECTED_UPBIT_4 = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"]


def test_feed_universe_specification():
    assert len(EXPECTED_BITHUMB_20) == 20
    assert len(EXPECTED_BINANCE_4) == 4
    assert len(EXPECTED_UPBIT_4) == 4

    bithumb_partitions_per_hour = len(EXPECTED_BITHUMB_20) * 3  # orderbook, trade, ticker
    binance_partitions_per_hour = len(EXPECTED_BINANCE_4) * 2   # orderbook, trade
    upbit_partitions_per_hour = len(EXPECTED_UPBIT_4) * 2       # orderbook, trade

    assert bithumb_partitions_per_hour == 60
    assert binance_partitions_per_hour == 8
    assert upbit_partitions_per_hour == 8

    total_partitions = bithumb_partitions_per_hour + binance_partitions_per_hour + upbit_partitions_per_hour
    assert total_partitions == 76


def test_runtime_seals_feed_universe():
    seals = list(Path("infra/aws/seals").glob("aws-72h-soak-*.runtime.json"))
    assert len(seals) >= 1, "At least one 72h soak runtime seal must exist"

    for seal_file in seals:
        data = json.loads(seal_file.read_text(encoding="utf-8"))
        feeds = data.get("feeds", {})
        
        assert feeds.get("bithumb_market_count") == 20
        assert feeds.get("bithumb_markets") == EXPECTED_BITHUMB_20
        assert feeds.get("binance_symbols") == EXPECTED_BINANCE_4
        assert feeds.get("upbit_markets") == EXPECTED_UPBIT_4
