"""Contract verification tests for normalized market data structures."""

import math
import pytest


def validate_orderbook_contract(record: dict) -> None:
    """Validate that an orderbook record adheres to the normalization contract."""
    required_fields = ["exchange", "symbol", "timestamp", "received_at", "bids", "asks"]
    for field in required_fields:
        assert field in record, f"Missing required field: {field}"

    assert record["exchange"] in ["bithumb", "binance", "upbit"]
    assert isinstance(record["timestamp"], int)
    assert isinstance(record["received_at"], int)
    assert record["timestamp"] > 0
    assert record["received_at"] > 0
    # Clock bound check: timestamp <= received_at + 10000ms
    assert record["timestamp"] <= record["received_at"] + 10000

    bids = record["bids"]
    asks = record["asks"]
    assert isinstance(bids, list) and len(bids) > 0
    assert isinstance(asks, list) and len(asks) > 0

    # Best bid < Best ask (no crossed book)
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    assert best_bid < best_ask, f"Crossed book: best_bid ({best_bid}) >= best_ask ({best_ask})"

    # Verify bids strictly descending
    for i in range(len(bids) - 1):
        p1, v1 = bids[i]
        p2, v2 = bids[i + 1]
        assert math.isfinite(p1) and p1 > 0
        assert math.isfinite(v1) and v1 > 0
        assert p1 > p2, f"Bids not descending: {p1} <= {p2}"

    # Verify asks strictly ascending
    for i in range(len(asks) - 1):
        p1, v1 = asks[i]
        p2, v2 = asks[i + 1]
        assert math.isfinite(p1) and p1 > 0
        assert math.isfinite(v1) and v1 > 0
        assert p1 < p2, f"Asks not ascending: {p1} >= {p2}"


def validate_trade_contract(record: dict) -> None:
    """Validate that a trade record adheres to the normalization contract."""
    required_fields = ["exchange", "symbol", "timestamp", "received_at", "side", "price", "volume"]
    for field in required_fields:
        assert field in record, f"Missing required field: {field}"

    assert record["exchange"] in ["bithumb", "binance", "upbit"]
    assert record["side"] in ["buy", "sell"]
    assert math.isfinite(record["price"]) and record["price"] > 0
    assert math.isfinite(record["volume"]) and record["volume"] > 0
    assert isinstance(record["timestamp"], int) and record["timestamp"] > 0
    assert isinstance(record["received_at"], int) and record["received_at"] > 0
    assert record["timestamp"] <= record["received_at"] + 10000


def validate_ticker_contract(record: dict) -> None:
    """Validate that a ticker record adheres to the normalization contract."""
    required_fields = ["exchange", "symbol", "timestamp", "received_at", "last_price"]
    for field in required_fields:
        assert field in record, f"Missing required field: {field}"

    assert record["exchange"] in ["bithumb", "binance", "upbit"]
    assert math.isfinite(record["last_price"]) and record["last_price"] > 0
    assert isinstance(record["timestamp"], int) and record["timestamp"] > 0
    assert isinstance(record["received_at"], int) and record["received_at"] > 0


def test_orderbook_contract_valid():
    ob = {
        "exchange": "bithumb",
        "symbol": "BTC-KRW",
        "timestamp": 1757001600100,
        "received_at": 1757001600120,
        "bids": [[85000000.0, 1.5], [84999000.0, 2.0]],
        "asks": [[85001000.0, 0.8], [85002000.0, 1.2]],
        "depth": 2,
    }
    validate_orderbook_contract(ob)


def test_orderbook_contract_crossed_fails():
    ob = {
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "timestamp": 1757001600100,
        "received_at": 1757001600120,
        "bids": [[50000.0, 1.5]],
        "asks": [[49999.0, 0.8]],  # Crossed!
    }
    with pytest.raises(AssertionError, match="Crossed book"):
        validate_orderbook_contract(ob)


def test_trade_contract_valid():
    tr = {
        "exchange": "upbit",
        "symbol": "BTC-KRW",
        "trade_id": "100234",
        "timestamp": 1757001600150,
        "received_at": 1757001600160,
        "side": "buy",
        "price": 85001000.0,
        "volume": 0.05,
    }
    validate_trade_contract(tr)


def test_trade_contract_invalid_side():
    tr = {
        "exchange": "bithumb",
        "symbol": "BTC-KRW",
        "timestamp": 1757001600150,
        "received_at": 1757001600160,
        "side": "unknown",
        "price": 85001000.0,
        "volume": 0.05,
    }
    with pytest.raises(AssertionError):
        validate_trade_contract(tr)


def test_ticker_contract_valid():
    tk = {
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "timestamp": 1757001600150,
        "received_at": 1757001600160,
        "last_price": 60123.45,
    }
    validate_ticker_contract(tk)
