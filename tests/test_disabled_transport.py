import pytest
from bithumb_coin_trader.order_transport import (
    DisabledLiveTransport,
    LiveTradingDisabledError,
    verify_no_live_credentials_in_offline_env,
)


def test_disabled_live_transport_fails_closed():
    transport = DisabledLiveTransport()

    with pytest.raises(LiveTradingDisabledError, match="Live trading transport is permanently disabled"):
        transport.send_order(market="KRW-BTC", side="BUY", price=100000000.0, qty=0.1)

    with pytest.raises(LiveTradingDisabledError, match="Live trading transport is permanently disabled"):
        transport.cancel_order("ord_123")

    with pytest.raises(LiveTradingDisabledError, match="Live trading transport is permanently disabled"):
        transport.fetch_balance()

    with pytest.raises(LiveTradingDisabledError, match="Live trading transport is permanently disabled"):
        transport.fetch_open_orders()


def test_credential_check_clean(monkeypatch):
    for k in ["BITHUMB_API_KEY", "BITHUMB_SECRET_KEY", "BINANCE_API_KEY"]:
        monkeypatch.delenv(k, raising=False)
    # Should not raise
    verify_no_live_credentials_in_offline_env()


def test_credential_check_raises_on_live_key(monkeypatch):
    monkeypatch.setenv("BITHUMB_API_KEY", "fake_live_key")
    with pytest.raises(LiveTradingDisabledError, match="Live trading credentials detected"):
        verify_no_live_credentials_in_offline_env()
