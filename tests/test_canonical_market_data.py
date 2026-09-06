import pytest
from pathlib import Path
from bithumb_coin_trader.canonical_market_data import (
    CanonicalOrderBook,
    CanonicalTrade,
    CanonicalTicker,
    TimestampSemantics,
    CanonicalDataValidationError,
    validate_canonical_orderbook,
    validate_canonical_trade,
    validate_canonical_ticker,
    upgrade_v1_dict_to_canonical_orderbook,
    upgrade_v1_dict_to_canonical_trade,
    write_canonical_ndjson_zstd,
    read_canonical_ndjson_zstd,
)


def test_canonical_orderbook_valid():
    ob = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1725500000000,
        receive_timestamp_ms=1725500000050,
        bids=((100_000_000.0, 1.5), (99_990_000.0, 2.0)),
        asks=((100_010_000.0, 1.2), (100_020_000.0, 3.0)),
    )
    validate_canonical_orderbook(ob)
    assert ob.best_bid == 100_000_000.0
    assert ob.best_ask == 100_010_000.0
    assert ob.mid_price == 100_005_000.0
    assert ob.spread == 10_000.0
    assert len(ob.compute_sha256()) == 64


def test_canonical_orderbook_validation_errors():
    # Crossed book
    crossed = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1010,
        bids=((100.0, 1.0),),
        asks=((99.0, 1.0),),
    )
    with pytest.raises(CanonicalDataValidationError, match="Crossed book"):
        validate_canonical_orderbook(crossed)

    # Unsorted bids
    unsorted_bids = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1010,
        bids=((90.0, 1.0), (95.0, 1.0)),
        asks=((100.0, 1.0),),
    )
    with pytest.raises(CanonicalDataValidationError, match="Bids not strictly descending"):
        validate_canonical_orderbook(unsorted_bids)

    # Unsorted asks
    unsorted_asks = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1010,
        bids=((90.0, 1.0),),
        asks=((105.0, 1.0), (100.0, 1.0)),
    )
    with pytest.raises(CanonicalDataValidationError, match="Asks not strictly ascending"):
        validate_canonical_orderbook(unsorted_asks)

    # Non-finite or negative
    bad_num = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1010,
        bids=((-90.0, 1.0),),
        asks=((100.0, 1.0),),
    )
    with pytest.raises(CanonicalDataValidationError, match="positive"):
        validate_canonical_orderbook(bad_num)


def test_canonical_trade_valid_and_invalid():
    t = CanonicalTrade(
        exchange="binance",
        market="BTCUSDT",
        trade_id="123456",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1020,
        price=60000.0,
        quantity=0.5,
        aggressor_side="BUY",
    )
    validate_canonical_trade(t)
    assert t.compute_sha256()

    with pytest.raises(CanonicalDataValidationError, match="Invalid aggressor side"):
        bad_trade = CanonicalTrade(
            exchange="binance",
            market="BTCUSDT",
            trade_id="123456",
            exchange_timestamp_ms=1000,
            receive_timestamp_ms=1020,
            price=60000.0,
            quantity=0.5,
            aggressor_side="UNKNOWN",
        )
        validate_canonical_trade(bad_trade)


def test_upgrade_v1_dict():
    raw_ob = {
        "exchange": "bithumb",
        "symbol": "KRW-BTC",
        "timestamp": 1725500000000,
        "received_at": 1725500000010,
        "bids": [[100000.0, 1.0], [99000.0, 2.0]],
        "asks": [[101000.0, 1.0], [102000.0, 2.0]],
    }
    canonical = upgrade_v1_dict_to_canonical_orderbook(raw_ob)
    assert canonical.market == "KRW-BTC"
    assert canonical.exchange == "bithumb"
    assert canonical.best_bid == 100000.0

    raw_trade = {
        "exchange": "upbit",
        "symbol": "KRW-BTC",
        "timestamp": 1725500000000,
        "price": 100000.0,
        "units": 0.25,
        "side": "buy",
    }
    can_trade = upgrade_v1_dict_to_canonical_trade(raw_trade)
    assert can_trade.aggressor_side == "BUY"
    assert can_trade.quantity == 0.25


def test_zstd_compression_roundtrip(tmp_path: Path):
    file_path = tmp_path / "orderbooks.ndjson.zst"
    ob1 = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1010,
        bids=((100.0, 1.0), (99.0, 2.0)),
        asks=((101.0, 1.0), (102.0, 2.0)),
    )
    ob2 = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1100,
        receive_timestamp_ms=1110,
        bids=((100.5, 1.0), (99.5, 2.0)),
        asks=((101.5, 1.0), (102.5, 2.0)),
    )
    count = write_canonical_ndjson_zstd(file_path, [ob1, ob2])
    assert count == 2

    recovered = list(read_canonical_ndjson_zstd(file_path, CanonicalOrderBook))
    assert len(recovered) == 2
    assert recovered[0].mid_price == ob1.mid_price
    assert recovered[1].mid_price == ob2.mid_price
    assert recovered[0].compute_sha256() == ob1.compute_sha256()


def test_golden_fixtures():
    import json
    fixture_dir = Path(__file__).parent / "fixtures" / "canonical_market_data"
    
    ob_path = fixture_dir / "bithumb_krw_btc_orderbook_golden.json"
    ob_data = json.loads(ob_path.read_text())
    ob = CanonicalOrderBook.from_dict(ob_data)
    assert ob.exchange == "bithumb"
    assert ob.mid_price == 100_005_000.0

    trade_path = fixture_dir / "binance_btcusdt_trade_golden.json"
    trade_data = json.loads(trade_path.read_text())
    trade = CanonicalTrade.from_dict(trade_data)
    assert trade.exchange == "binance"
    assert trade.aggressor_side == "BUY"

    ticker_path = fixture_dir / "upbit_krw_btc_ticker_golden.json"
    ticker_data = json.loads(ticker_path.read_text())
    ticker = CanonicalTicker.from_dict(ticker_data)
    assert ticker.exchange == "upbit"
    assert ticker.last_price == 100005000.0

