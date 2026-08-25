from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import unittest
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast

from bithumb_coin_trader.bithumb_websocket import (
    PRIVATE_URL,
    PUBLIC_URL,
    AssetBalance,
    BithumbWebSocketObserver,
    MyAssetObservation,
    MyOrderObservation,
    ObservationCache,
    ObservationValidationError,
    OrderbookObservation,
    ReconnectBackoff,
    TickerObservation,
    WebSocketTransport,
    WebSocketProtocolError,
    build_private_subscription,
    build_public_subscription,
    create_hs256_jwt,
    decode_frame,
    encode_frame,
    parse_observation,
)


class SubscriptionTests(unittest.TestCase):
    def test_public_subscription_uses_v1_default_realtime_streams(self) -> None:
        payload = build_public_subscription(["KRW-BTC", "KRW-ETH"], ticket="scanner")
        self.assertEqual(
            payload,
            [
                {"ticket": "scanner"},
                {
                    "type": "ticker",
                    "codes": ["KRW-BTC", "KRW-ETH"],
                    "is_only_realtime": True,
                },
                {
                    "type": "orderbook",
                    "codes": ["KRW-BTC", "KRW-ETH"],
                    "is_only_realtime": True,
                },
                {"format": "DEFAULT"},
            ],
        )
        self.assertEqual(PUBLIC_URL, "wss://ws-api.bithumb.com/websocket/v1")

    def test_private_subscription_supports_all_market_orders_and_assets(self) -> None:
        self.assertEqual(
            build_private_subscription(ticket="private"),
            [
                {"ticket": "private"},
                {"type": "myOrder", "codes": []},
                {"type": "myAsset"},
                {"format": "DEFAULT"},
            ],
        )
        self.assertEqual(PRIVATE_URL, "wss://ws-api.bithumb.com/websocket/v2/private")

    def test_subscriptions_reject_unsafe_or_empty_selection(self) -> None:
        with self.assertRaises(ValueError):
            build_public_subscription(["krw-btc"])
        with self.assertRaises(ValueError):
            build_public_subscription(["KRW-BTC"], ticker=False, orderbook=False)
        with self.assertRaises(ValueError):
            build_private_subscription(my_order=False, my_asset=False)


class JwtAndFrameTests(unittest.TestCase):
    def test_hs256_jwt_has_expected_payload_and_signature(self) -> None:
        token = create_hs256_jwt(
            "access", "secret", nonce="fixed-nonce", timestamp_ms=1_725_000_000_000
        )
        header, payload, signature = token.split(".")

        def decode(value: str) -> dict[str, object]:
            padded = value + "=" * (-len(value) % 4)
            return json.loads(base64.urlsafe_b64decode(padded))

        self.assertEqual(decode(header), {"alg": "HS256", "typ": "JWT"})
        self.assertEqual(
            decode(payload),
            {
                "access_key": "access",
                "nonce": "fixed-nonce",
                "timestamp": 1_725_000_000_000,
            },
        )
        expected = base64.urlsafe_b64encode(
            hmac.new(b"secret", f"{header}.{payload}".encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        self.assertEqual(signature, expected)

    def test_frame_codec_handles_masking_and_extended_lengths(self) -> None:
        payload = "한" * 100
        encoded = encode_frame(payload, mask=True, mask_key=b"abcd")
        frame, consumed = decode_frame(encoded)
        self.assertEqual(consumed, len(encoded))
        self.assertEqual(frame.payload.decode(), payload)
        self.assertEqual(frame.opcode, 1)
        self.assertTrue(frame.fin)

    def test_ping_frame_is_a_masked_control_frame(self) -> None:
        encoded = encode_frame(b"health", opcode=0x9, mask=True, mask_key=b"abcd")
        frame, consumed = decode_frame(encoded)
        self.assertEqual(consumed, len(encoded))
        self.assertEqual(frame.opcode, 0x9)
        self.assertEqual(frame.payload, b"health")

    def test_rejects_masked_server_frame_and_oversized_control_frame(self) -> None:
        with self.assertRaisesRegex(WebSocketProtocolError, "server frames"):
            decode_frame(encode_frame("x", mask_key=b"abcd"), require_unmasked=True)
        with self.assertRaises(ValueError):
            encode_frame(b"x" * 126, opcode=0x9)

    def test_transport_accepts_bithumb_utf8_json_binary_frame(self) -> None:
        transport = WebSocketTransport()
        transport._buffer = encode_frame('{"type":"ticker"}', opcode=0x2, mask=False)

        self.assertEqual(transport.receive_text(), '{"type":"ticker"}')


class ObservationParserTests(unittest.TestCase):
    def test_parses_public_ticker_and_orderbook(self) -> None:
        ticker = parse_observation(
            {
                "type": "ticker",
                "code": "KRW-BTC",
                "trade_price": 150000000,
                "trade_volume": 0.01,
                "signed_change_rate": "-0.012",
                "acc_trade_price_24h": "123456789",
                "timestamp": 1725927377931,
                "stream_type": "REALTIME",
            }
        ).observation
        self.assertIsInstance(ticker, TickerObservation)
        assert isinstance(ticker, TickerObservation)
        self.assertEqual(ticker.trade_price, Decimal("150000000"))
        self.assertEqual(ticker.signed_change_rate, Decimal("-0.012"))
        orderbook = parse_observation(
            {
                "type": "orderbook",
                "code": "KRW-BTC",
                "timestamp": 1725927377931000,
                "stream_type": "REALTIME",
                "orderbook_units": [
                    {
                        "ask_price": 151000000,
                        "bid_price": 150900000,
                        "ask_size": "0.2",
                        "bid_size": "0.3",
                    }
                ],
            }
        ).observation
        self.assertIsInstance(orderbook, OrderbookObservation)
        assert isinstance(orderbook, OrderbookObservation)
        self.assertEqual(orderbook.levels[0].bid_size, Decimal("0.3"))

    def test_private_order_is_observation_with_reconciliation_hint(self) -> None:
        event = parse_observation(
            {
                "type": "myOrder",
                "stream_type": "REALTIME",
                "code": "KRW-BTC",
                "order_id": "order-1",
                "client_order_id": "client-1",
                "side": "buy",
                "order_type": "limit",
                "state": "trade",
                "order_price": 100000000,
                "order_quantity": "0.5",
                "order_amount": 50000000,
                "order_timestamp": 1735689600000,
                "trade_id": "trade-1",
                "trade_price": 100000000,
                "trade_quantity": "0.3",
                "executed_quantity": "0.3",
                "remaining_quantity": "0.2",
                "timestamp": 1735689612400,
            }
        )
        self.assertIsInstance(event.observation, MyOrderObservation)
        self.assertEqual(event.reconciliation_hints[0].scope, "order")
        self.assertEqual(event.reconciliation_hints[0].order_id, "order-1")

    def test_private_asset_is_observation_with_assets_hint(self) -> None:
        event = parse_observation(
            json.dumps(
                {
                    "type": "myAsset",
                    "stream_type": "REALTIME",
                    "assets": [{"currency": "KRW", "balance": "20000", "locked": "0"}],
                    "asset_timestamp": 1727052537592,
                    "timestamp": 1727052537687,
                }
            )
        )
        self.assertIsInstance(event.observation, MyAssetObservation)
        asset_observation = cast(MyAssetObservation, event.observation)
        self.assertEqual(asset_observation.assets[0], AssetBalance("KRW", Decimal("20000"), Decimal("0")))
        self.assertEqual(event.reconciliation_hints[0].scope, "assets")

    def test_invalid_private_state_and_non_finite_values_fail_closed(self) -> None:
        with self.assertRaises(ObservationValidationError):
            parse_observation(
                {
                    "type": "ticker",
                    "code": "KRW-BTC",
                    "trade_price": "NaN",
                    "trade_volume": 0,
                    "timestamp": 1,
                    "stream_type": "REALTIME",
                }
            )
        with self.assertRaisesRegex(ObservationValidationError, "trade state requires"):
            parse_observation(
                {
                    "type": "myOrder",
                    "code": "KRW-BTC",
                    "order_id": "x",
                    "side": "buy",
                    "order_type": "limit",
                    "state": "trade",
                    "stream_type": "REALTIME",
                }
            )


class CacheAndReconnectTests(unittest.TestCase):
    def test_cache_is_snapshot_based_and_tracks_health(self) -> None:
        cache = ObservationCache()
        now = datetime(2026, 8, 25, tzinfo=timezone.utc)
        cache.connected(now)
        event = parse_observation(
            {
                "type": "ticker",
                "code": "KRW-BTC",
                "trade_price": 1,
                "trade_volume": 0,
                "timestamp": 1,
                "stream_type": "REALTIME",
            }
        )
        cache.record(event)
        first = cache.snapshot()
        self.assertTrue(first.health.connected)
        self.assertEqual(first.health.message_count, 1)
        self.assertIn("KRW-BTC", first.tickers)
        cache.disconnected("network")
        self.assertTrue(first.health.connected)
        self.assertFalse(cache.snapshot().health.connected)
        self.assertEqual(cache.snapshot().health.reconnect_count, 1)

    def test_backoff_is_bounded_and_deterministic_when_jitter_supplied(self) -> None:
        backoff = ReconnectBackoff(1, 10, 2, 0.2)
        self.assertEqual(backoff.delay(0, random_fraction=0.5), 1)
        self.assertEqual(backoff.delay(10, random_fraction=0.5), 10)
        self.assertEqual(backoff.delay(0, random_fraction=1), 1.2)

    def test_observer_callback_receives_append_only_event(self) -> None:
        callbacks = []

        class FakeTransport:
            def __init__(self) -> None:
                self.messages = iter(
                    [
                        json.dumps(
                            {
                                "type": "ticker",
                                "code": "KRW-BTC",
                                "trade_price": 1,
                                "trade_volume": 0,
                                "timestamp": 1,
                                "stream_type": "REALTIME",
                            }
                        )
                    ]
                )

            def connect(
                self, url: str, headers: Mapping[str, str] | None = None
            ) -> None:
                self.url = url

            def send_json(self, value: Any) -> None:
                self.payload = value

            def receive_text(self) -> str:
                try:
                    return next(self.messages)
                except StopIteration:
                    stop.set()
                    raise EOFError("done")

            def send_ping(self, payload: bytes = b"health") -> None:
                pass

            def close(self) -> None:
                pass

        stop = threading.Event()
        observer = BithumbWebSocketObserver(
            build_public_subscription(["KRW-BTC"], ticket="test"),
            callback=callbacks.append,
            backoff=ReconnectBackoff(0.001, 0.001, 1, 0),
            transport_factory=FakeTransport,
        )
        observer.run_forever(stop)
        self.assertEqual(len(callbacks), 1)
        self.assertIsInstance(callbacks[0].observation, TickerObservation)
        self.assertEqual(observer.cache.snapshot().health.message_count, 1)


if __name__ == "__main__":
    unittest.main()
