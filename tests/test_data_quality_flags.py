import pytest
from bithumb_coin_trader.canonical_market_data import CanonicalOrderBook
from bithumb_coin_trader.data_quality_flags import (
    DataQualityFlag,
    scan_orderbook_quality,
    filter_clean_orderbooks,
)


def test_data_quality_flags_clean():
    ob = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1010,
        bids=((100_000_000.0, 1.0),),
        asks=((100_010_000.0, 1.0),),
    )
    flags = scan_orderbook_quality(ob, prev_receive_ms=1000)
    assert flags == DataQualityFlag.CLEAN


def test_data_quality_clock_jump_and_gap():
    ob1 = CanonicalOrderBook("bithumb", "KRW-BTC", 1000, 1000, ((100.0, 1.0),), ((101.0, 1.0),))
    ob_jump = CanonicalOrderBook("bithumb", "KRW-BTC", 900, 950, ((100.0, 1.0),), ((101.0, 1.0),))
    ob_gap = CanonicalOrderBook("bithumb", "KRW-BTC", 8000, 8000, ((100.0, 1.0),), ((101.0, 1.0),))

    flags_jump = scan_orderbook_quality(ob_jump, prev_receive_ms=1000)
    assert DataQualityFlag.CLOCK_JUMP_BACKWARD in flags_jump

    flags_gap = scan_orderbook_quality(ob_gap, prev_receive_ms=1000, max_gap_ms=5000)
    assert DataQualityFlag.LARGE_RECEIVE_GAP in flags_gap


def test_data_quality_crossed_and_extreme_spread():
    ob_crossed = CanonicalOrderBook("bithumb", "KRW-BTC", 1000, 1000, ((101.0, 1.0),), ((100.0, 1.0),), is_snapshot=True)
    flags_crossed = scan_orderbook_quality(ob_crossed)
    assert DataQualityFlag.CROSSED_BOOK in flags_crossed

    ob_wide = CanonicalOrderBook("bithumb", "KRW-BTC", 1000, 1000, ((100.0, 1.0),), ((105.0, 1.0),))
    flags_wide = scan_orderbook_quality(ob_wide, max_spread_bps=100.0)
    assert DataQualityFlag.EXTREME_SPREAD in flags_wide


def test_filter_clean_orderbooks():
    ob1 = CanonicalOrderBook("bithumb", "KRW-BTC", 1000, 1000, ((100.0, 1.0),), ((101.0, 1.0),))
    ob2_jump = CanonicalOrderBook("bithumb", "KRW-BTC", 900, 900, ((100.0, 1.0),), ((101.0, 1.0),))
    ob3 = CanonicalOrderBook("bithumb", "KRW-BTC", 2000, 2000, ((100.0, 1.0),), ((101.0, 1.0),))

    clean = filter_clean_orderbooks([ob1, ob2_jump, ob3])
    assert len(clean) == 2
    assert clean[0].receive_timestamp_ms == 1000
    assert clean[1].receive_timestamp_ms == 2000
