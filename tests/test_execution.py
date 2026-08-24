from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bithumb_coin_trader.execution import (
    BithumbExecutor,
    LIVE_CONFIRMATION_TOKEN,
    ExecutionError,
    ExecutionPlan,
    LiveTradingDisabledError,
    OrderChanceError,
    Position,
    RiskRejectedError,
    TradeIntent,
    UnsupportedPositionError,
    plan_execution,
)
from bithumb_coin_trader.fill_ledger import FillLedger, FillLedgerError
from bithumb_coin_trader.config import TradingMode, TradingSettings
from bithumb_coin_trader.discord_notify import TradeEvent, TradeNotification
from bithumb_coin_trader.mcp_client import (
    ALLOWED_CHILD_ENV,
    DEFAULT_COMMAND,
    LIVE_COMMAND,
    UnsafeToolError,
    minimal_child_env,
)
from bithumb_coin_trader.models import Signal
from bithumb_coin_trader.risk import RiskContext
from bithumb_coin_trader.state import BotState, load_state, save_state


def chance_result(*, krw_balance: str = "20000", btc_balance: str = "1") -> dict[str, object]:
    payload = {
        "bid_fee": "0.0004",
        "ask_fee": "0.0004",
        "market": {
            "id": "KRW-BTC",
            "state": "active",
            "order_sides": ["ask", "bid"],
            "bid_types": ["limit", "price"],
            "ask_types": ["limit", "market"],
            "bid": {"currency": "KRW", "min_total": "5000"},
            "ask": {"currency": "BTC", "min_total": "5000"},
        },
        "bid_account": {"currency": "KRW", "balance": krw_balance},
        "ask_account": {"currency": "BTC", "balance": btc_balance},
    }
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def order_result(
    client_order_id: str,
    state: str,
    *,
    side: str = "bid",
    executed_volume: str = "0.1",
    market: str = "KRW-BTC",
) -> dict[str, object]:
    payload = {
        "client_order_id": client_order_id,
        "state": state,
        "side": side,
        "executed_volume": executed_volume,
        "market": market,
    }
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


class FakeClient:
    def __init__(
        self, chance: object | None = None, order: object | None = None
    ) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.chance = chance_result() if chance is None else chance
        self.order = order

    def call_read_tool(self, name: str, arguments: dict[str, str]) -> object:
        self.calls.append((name, arguments))
        if name == "account_get_order_chance":
            return self.chance
        if name == "trade_get_order" and self.order is not None:
            return self.order
        raise AssertionError(f"unexpected read tool: {name}")

    def call_tool(self, name: str, arguments: dict[str, str]) -> dict[str, str]:
        self.calls.append((name, arguments))
        return {"order_id": "server-order-id"}


class SequentialOrderClient(FakeClient):
    def __init__(self, orders: list[object]) -> None:
        super().__init__()
        self.orders = iter(orders)

    def call_read_tool(self, name: str, arguments: dict[str, str]) -> object:
        self.calls.append((name, arguments))
        if name == "trade_get_order":
            return next(self.orders)
        return super().call_read_tool(name, arguments)


class FakeReadClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def _call_tool(self, name: str, arguments: object = None) -> object:
        self.calls.append((name, arguments))
        return {"content": []}

    # Exercise the same allow-list contract without starting a subprocess.
    from bithumb_coin_trader.mcp_client import McpStdioClient

    call_read_tool = McpStdioClient.call_read_tool
    call_tool = McpStdioClient.call_tool


class FailingPreflightClient(FakeClient):
    def call_read_tool(self, name: str, arguments: dict[str, str]) -> object:
        self.calls.append((name, arguments))
        raise RuntimeError("preflight unavailable")


class AmbiguousWriteClient(FakeClient):
    def call_tool(self, name: str, arguments: dict[str, str]) -> object:
        self.calls.append((name, arguments))
        raise TimeoutError("write outcome unknown")


class FakeNotifier:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[TradeNotification] = []
        self.fail = fail

    def send(self, notification: TradeNotification) -> bool:
        self.events.append(notification)
        if self.fail:
            raise RuntimeError("Discord offline")
        return True


class TradeIntentTests(unittest.TestCase):
    def test_rejects_bad_market_non_exact_position_and_float_money(self) -> None:
        with self.assertRaises(ValueError):
            TradeIntent("btc-krw", "LONG", quote_amount="10000")
        with self.assertRaises(ValueError):
            TradeIntent("KRW-BTC", "long", quote_amount="10000")
        with self.assertRaises(ValueError):
            TradeIntent("KRW-BTC", "LONG", quote_amount=0.1)

    def test_rejects_non_finite_and_non_positive_amounts(self) -> None:
        for value in ("NaN", "Infinity", "0", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TradeIntent("KRW-BTC", "LONG", quote_amount=value)


class PlanningTests(unittest.TestCase):
    def test_flat_to_long_is_market_buy_with_client_order_id(self) -> None:
        intent = TradeIntent("KRW-BTC", Position.LONG, quote_amount=Decimal("10000"))
        plan = plan_execution(intent, Position.FLAT, client_order_id="intent-123")
        self.assertEqual(plan.tool_name, "trade_place_order")
        self.assertEqual(
            plan.arguments,
            {
                "market": "KRW-BTC",
                "side": "bid",
                "order_type": "price",
                "price": "10000",
                "client_order_id": "intent-123",
            },
        )

    def test_long_to_flat_is_market_sell(self) -> None:
        intent = TradeIntent("KRW-BTC", "FLAT", base_volume="0.001")
        plan = plan_execution(intent, "LONG", client_order_id="intent-124")
        self.assertEqual(plan.arguments["side"], "ask")
        self.assertEqual(plan.arguments["order_type"], "market")
        self.assertEqual(plan.arguments["volume"], "0.001")

    def test_same_position_is_noop(self) -> None:
        plan = plan_execution(TradeIntent("KRW-BTC", "FLAT"), "FLAT")
        self.assertTrue(plan.is_noop)

    def test_short_fails_closed(self) -> None:
        with self.assertRaisesRegex(UnsupportedPositionError, "SHORT"):
            plan_execution(TradeIntent("KRW-BTC", "SHORT"), "FLAT")
        with self.assertRaisesRegex(UnsupportedPositionError, "SHORT"):
            plan_execution(TradeIntent("KRW-BTC", "FLAT"), "SHORT")


class ExecutionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.state_path = Path(self.temporary_directory.name) / "state.json"
        self.client = FakeClient()
        self.plan = plan_execution(
            TradeIntent("KRW-BTC", "LONG", quote_amount="10000"),
            "FLAT",
            client_order_id="intent-gated",
        )

    @staticmethod
    def live_settings() -> TradingSettings:
        return TradingSettings(mode=TradingMode.LIVE, live_trading_enabled=True)

    @staticmethod
    def live_env() -> dict[str, str]:
        return {"BITHUMB_LIVE_TRADING": "true", "BITHUMB_NEW_ENTRIES": "true"}

    @staticmethod
    def risk_context(**overrides: object) -> RiskContext:
        values = {
            "requested_side": Signal.LONG,
            "requested_notional_krw": 10_000,
            "current_equity_krw": 20_000,
            "start_of_day_equity_krw": 20_000,
            "peak_equity_krw": 20_000,
            "daily_entries": 0,
            "data_is_fresh": True,
        }
        values.update(overrides)
        return RiskContext(**values)

    @staticmethod
    def state(**overrides: object) -> BotState:
        values = {"position": "flat", "position_volume": "0"}
        values.update(overrides)
        return BotState(**values)

    def test_paper_is_default_and_never_calls_client(self) -> None:
        result = BithumbExecutor(
            self.client,
            state_path=self.state_path,
            env={"BITHUMB_LIVE_TRADING": "true"},
        ).execute(
            self.plan,
            risk_context=self.risk_context(),
            bot_state=self.state(),
            confirmation_token=LIVE_CONFIRMATION_TOKEN,
        )
        self.assertFalse(result.submitted)
        self.assertEqual(self.client.calls, [])

    def test_live_default_loads_local_discord_notifier(self) -> None:
        with patch("bithumb_coin_trader.execution.DiscordNotifier") as notifier:
            executor = BithumbExecutor(
                self.client,
                state_path=self.state_path,
                settings=self.live_settings(),
            )
        notifier.assert_called_once_with()
        self.assertIs(executor.notifier, notifier.return_value)

    def test_live_requires_exact_env_value(self) -> None:
        for env in ({}, {"BITHUMB_LIVE_TRADING": "TRUE"}, {"BITHUMB_LIVE_TRADING": "1"}):
            with self.subTest(env=env), self.assertRaises(LiveTradingDisabledError):
                BithumbExecutor(
                    self.client,
                    state_path=self.state_path,
                    settings=self.live_settings(),
                    env=env,
                ).execute(
                    self.plan,
                    risk_context=self.risk_context(),
                    bot_state=self.state(),
                    confirmation_token=LIVE_CONFIRMATION_TOKEN,
                )
        self.assertEqual(self.client.calls, [])

    def test_live_requires_exact_runtime_token(self) -> None:
        executor = BithumbExecutor(
            self.client,
            state_path=self.state_path,
            settings=self.live_settings(),
            env=self.live_env(),
        )
        for token in (None, "confirm_bithumb_live_order", " CONFIRM_BITHUMB_LIVE_ORDER"):
            with self.subTest(token=token), self.assertRaises(LiveTradingDisabledError):
                executor.execute(
                    self.plan,
                    risk_context=self.risk_context(),
                    bot_state=self.state(),
                    confirmation_token=token,
                )
        self.assertEqual(self.client.calls, [])

    def test_new_exposure_requires_separate_exact_switch(self) -> None:
        for env in (
            {"BITHUMB_LIVE_TRADING": "true"},
            {"BITHUMB_LIVE_TRADING": "true", "BITHUMB_NEW_ENTRIES": "TRUE"},
        ):
            with self.subTest(env=env), self.assertRaisesRegex(
                LiveTradingDisabledError, "BITHUMB_NEW_ENTRIES"
            ):
                BithumbExecutor(
                    self.client,
                    state_path=self.state_path,
                    settings=self.live_settings(),
                    env=env,
                ).execute(
                    self.plan,
                    risk_context=self.risk_context(),
                    bot_state=self.state(),
                    confirmation_token=LIVE_CONFIRMATION_TOKEN,
                )
        self.assertEqual(self.client.calls, [])

    def test_all_three_gates_allow_one_identified_call_on_fake_only(self) -> None:
        result = BithumbExecutor(
            self.client,
            state_path=self.state_path,
            settings=self.live_settings(),
            env=self.live_env(),
        ).execute(
            self.plan,
            risk_context=self.risk_context(),
            bot_state=self.state(),
            confirmation_token=LIVE_CONFIRMATION_TOKEN,
        )
        self.assertTrue(result.submitted)
        self.assertEqual(len(self.client.calls), 2)
        self.assertEqual(self.client.calls[0][0], "account_get_order_chance")
        self.assertEqual(self.client.calls[1][0], "trade_place_order")
        self.assertEqual(self.client.calls[1][1]["client_order_id"], "intent-gated")
        persisted = load_state(self.state_path)
        self.assertEqual(persisted.active_client_order_id, "intent-gated")
        self.assertFalse(persisted.untracked_order)

    def test_order_notifications_are_typed_and_never_change_order_outcome(self) -> None:
        notifier = FakeNotifier(fail=True)
        save_state(self.state_path, self.state())
        result = BithumbExecutor(
            self.client,
            state_path=self.state_path,
            settings=self.live_settings(),
            env=self.live_env(),
            notifier=notifier,
        ).execute(
            self.plan,
            risk_context=self.risk_context(),
            bot_state=self.state(),
            confirmation_token=LIVE_CONFIRMATION_TOKEN,
        )
        self.assertTrue(result.submitted)
        self.assertEqual([event.event for event in notifier.events], [TradeEvent.ACCEPTED])
        self.assertEqual(notifier.events[0].client_order_id, "intent-gated")

    def test_unidentified_plan_fails_closed(self) -> None:
        plan = ExecutionPlan(
            "KRW-BTC", Position.FLAT, Position.LONG, "trade_place_order", {}, None
        )
        with self.assertRaisesRegex(Exception, "unidentifiable"):
            BithumbExecutor(
                self.client,
                state_path=self.state_path,
                settings=self.live_settings(),
                env=self.live_env(),
            ).execute(
                plan,
                risk_context=self.risk_context(),
                bot_state=self.state(),
                confirmation_token=LIVE_CONFIRMATION_TOKEN,
            )
        self.assertEqual(self.client.calls, [])

    def test_failed_preflight_never_reaches_order_call(self) -> None:
        client = FailingPreflightClient()
        notifier = FakeNotifier()
        with self.assertRaisesRegex(RuntimeError, "preflight unavailable"):
            BithumbExecutor(
                client,
                state_path=self.state_path,
                settings=self.live_settings(),
                env=self.live_env(),
                notifier=notifier,
            ).execute(
                self.plan,
                risk_context=self.risk_context(),
                bot_state=self.state(),
                confirmation_token=LIVE_CONFIRMATION_TOKEN,
            )
        self.assertEqual([call[0] for call in client.calls], ["account_get_order_chance"])
        self.assertEqual(notifier.events[0].event, TradeEvent.BLOCKED)
        self.assertEqual(notifier.events[0].detail, "RuntimeError")

    def test_tampered_payload_fails_before_preflight(self) -> None:
        tampered = ExecutionPlan(
            self.plan.market,
            self.plan.current,
            self.plan.target,
            self.plan.tool_name,
            {**self.plan.arguments, "side": "ask"},
            self.plan.client_order_id,
        )
        with self.assertRaisesRegex(Exception, "side or order_type"):
            BithumbExecutor(
                self.client,
                state_path=self.state_path,
                settings=self.live_settings(),
                env=self.live_env(),
            ).execute(
                tampered,
                risk_context=self.risk_context(),
                bot_state=self.state(),
                confirmation_token=LIVE_CONFIRMATION_TOKEN,
            )
        self.assertEqual(self.client.calls, [])

    def execute_live(
        self,
        *,
        client: FakeClient | None = None,
        risk_context: RiskContext | None = None,
        bot_state: BotState | None = None,
        notifier: FakeNotifier | None = None,
    ) -> object:
        selected_state = bot_state or self.state()
        save_state(self.state_path, selected_state)
        return BithumbExecutor(
            client or self.client,
            state_path=self.state_path,
            settings=self.live_settings(),
            env=self.live_env(),
            notifier=notifier,
        ).execute(
            self.plan,
            risk_context=risk_context or self.risk_context(),
            bot_state=selected_state,
            confirmation_token=LIVE_CONFIRMATION_TOKEN,
        )

    def test_fresh_risk_and_state_are_enforced_before_mcp(self) -> None:
        cases = (
            (self.risk_context(data_is_fresh=False), self.state(), "stale"),
            (self.risk_context(daily_entries=1), self.state(), "daily entry"),
            (
                self.risk_context(current_equity_krw=19_000),
                self.state(),
                "daily loss",
            ),
            (
                self.risk_context(current_equity_krw=17_000),
                self.state(),
                "drawdown",
            ),
            (self.risk_context(), self.state(untracked_order=True), "untracked"),
            (
                self.risk_context(),
                self.state(
                    active_client_order_id="existing-order",
                    pending_order_side="bid",
                    pending_market="KRW-BTC",
                ),
                "untracked",
            ),
        )
        for risk, state, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                RiskRejectedError, message
            ):
                self.execute_live(risk_context=risk, bot_state=state)
        self.assertEqual(self.client.calls, [])

    def test_non_finite_risk_values_fail_before_mcp(self) -> None:
        with self.assertRaisesRegex(RiskRejectedError, "finite"):
            self.execute_live(
                risk_context=self.risk_context(current_equity_krw=float("nan"))
            )
        self.assertEqual(self.client.calls, [])

    def test_hard_ten_thousand_krw_cap_is_enforced(self) -> None:
        larger_plan = plan_execution(
            TradeIntent("KRW-BTC", "LONG", quote_amount="10001"),
            "FLAT",
            client_order_id="too-large",
        )
        with self.assertRaisesRegex(RiskRejectedError, "10,000"):
            BithumbExecutor(
                self.client,
                state_path=self.state_path,
                settings=self.live_settings(),
                env=self.live_env(),
            ).execute(
                larger_plan,
                risk_context=self.risk_context(requested_notional_krw=10_001),
                bot_state=self.state(),
                confirmation_token=LIVE_CONFIRMATION_TOKEN,
            )
        self.assertEqual(self.client.calls, [])

    def test_order_chance_rejects_insufficient_or_malformed_responses(self) -> None:
        malformed_cases = (
            {},
            {"content": [{"type": "text", "text": "not-json"}]},
            chance_result(krw_balance="9999"),
            chance_result(krw_balance="10000"),
        )
        for response in malformed_cases:
            client = FakeClient(response)
            with self.subTest(response=response), self.assertRaises(OrderChanceError):
                self.execute_live(client=client)
            self.assertEqual([call[0] for call in client.calls], ["account_get_order_chance"])

    def test_order_chance_rejects_wrong_market_minimum_fee_and_balance(self) -> None:
        base = json.loads(chance_result()["content"][0]["text"])
        mutations = []
        for mutate in (
            lambda value: value["market"].update(id="KRW-ETH"),
            lambda value: value["market"].update(state="halted"),
            lambda value: value["market"].update(bid_types=["limit"]),
            lambda value: value["market"]["bid"].update(min_total="11000"),
            lambda value: value.pop("bid_fee"),
            lambda value: value.update(bid_fee="missing"),
            lambda value: value.update(bid_fee="0.003"),
            lambda value: value["bid_account"].update(balance="NaN"),
        ):
            value = json.loads(json.dumps(base))
            mutate(value)
            mutations.append({"content": [{"type": "text", "text": json.dumps(value)}]})
        for response in mutations:
            client = FakeClient(response)
            with self.subTest(response=response), self.assertRaises(OrderChanceError):
                self.execute_live(client=client)
            self.assertEqual([call[0] for call in client.calls], ["account_get_order_chance"])

    def test_order_chance_accepts_real_mcp_nested_wrapper(self) -> None:
        inner = json.loads(chance_result()["content"][0]["text"])
        wrapped = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "tool": "account_get_order_chance",
                            "ok": True,
                            "data": {
                                "endpoint": "GET /v1/orders/chance",
                                "requestTime": "2026-08-11T00:00:00Z",
                                "data": inner,
                            },
                        }
                    ),
                }
            ]
        }
        client = FakeClient(wrapped)
        result = self.execute_live(client=client)
        self.assertTrue(result.submitted)
        self.assertEqual(
            [call[0] for call in client.calls],
            ["account_get_order_chance", "trade_place_order"],
        )

    def test_long_to_flat_checks_ask_fee_minimum_and_asset_balance(self) -> None:
        plan = plan_execution(
            TradeIntent("KRW-BTC", "FLAT", base_volume="0.1"),
            "LONG",
            client_order_id="sell-safe",
        )
        long_state = BotState(position="long", position_volume="0.1")
        save_state(self.state_path, long_state)
        result = BithumbExecutor(
            self.client,
            state_path=self.state_path,
            settings=self.live_settings(),
            env={"BITHUMB_LIVE_TRADING": "true"},
        ).execute(
            plan,
            risk_context=self.risk_context(
                requested_side=Signal.FLAT,
                requested_notional_krw=10_000,
                reference_price_krw=100_000,
            ),
            bot_state=long_state,
            confirmation_token=LIVE_CONFIRMATION_TOKEN,
        )
        self.assertTrue(result.submitted)
        self.assertEqual([call[0] for call in self.client.calls], [
            "account_get_order_chance",
            "trade_place_order",
        ])

    def test_sell_is_bound_to_tracked_volume_reference_price_and_notional(self) -> None:
        plan = plan_execution(
            TradeIntent("KRW-BTC", "FLAT", base_volume="0.1"),
            "LONG",
            client_order_id="sell-bound",
        )
        cases = (
            (
                BotState(position="long", position_volume="0.2"),
                self.risk_context(
                    requested_side=Signal.FLAT,
                    requested_notional_krw=10_000,
                    reference_price_krw=100_000,
                ),
                "tracked position volume",
            ),
            (
                BotState(position="long", position_volume="0.1"),
                self.risk_context(
                    requested_side=Signal.FLAT, requested_notional_krw=10_000
                ),
                "reference_price",
            ),
            (
                BotState(position="long", position_volume="0.1"),
                self.risk_context(
                    requested_side=Signal.FLAT,
                    requested_notional_krw=9_000,
                    reference_price_krw=100_000,
                ),
                "volume times reference price",
            ),
        )
        for state, risk, message in cases:
            save_state(self.state_path, state)
            with self.subTest(message=message), self.assertRaisesRegex(
                RiskRejectedError, message
            ):
                BithumbExecutor(
                    self.client,
                    state_path=self.state_path,
                    settings=self.live_settings(),
                    env=self.live_env(),
                ).execute(
                    plan,
                    risk_context=risk,
                    bot_state=state,
                    confirmation_token=LIVE_CONFIRMATION_TOKEN,
                )
        self.assertEqual(self.client.calls, [])

    def test_ambiguous_write_persists_active_untracked_without_retry(self) -> None:
        client = AmbiguousWriteClient()
        notifier = FakeNotifier()
        with self.assertRaisesRegex(TimeoutError, "unknown"):
            self.execute_live(client=client, notifier=notifier)
        persisted = load_state(self.state_path)
        self.assertEqual(persisted.active_client_order_id, "intent-gated")
        self.assertEqual(persisted.pending_order_side, "bid")
        self.assertEqual(persisted.pending_market, "KRW-BTC")
        self.assertTrue(persisted.untracked_order)
        self.assertEqual(
            [call[0] for call in client.calls],
            ["account_get_order_chance", "trade_place_order"],
        )
        self.assertEqual([event.event for event in notifier.events], [TradeEvent.AMBIGUOUS])

    def test_reconcile_clears_only_known_terminal_order(self) -> None:
        active = self.state(
            active_client_order_id="intent-gated",
            pending_order_side="bid",
            pending_market="KRW-BTC",
            untracked_order=True,
        )
        save_state(self.state_path, active)
        waiting_client = FakeClient(order=order_result("intent-gated", "wait"))
        waiting = BithumbExecutor(
            waiting_client, state_path=self.state_path
        ).reconcile_active_order()
        self.assertEqual(waiting, active)
        self.assertEqual(len(waiting_client.calls), 1)

        done_client = FakeClient(order=order_result("intent-gated", "done"))
        reconciled = BithumbExecutor(
            done_client, state_path=self.state_path
        ).reconcile_active_order()
        self.assertIsNone(reconciled.active_client_order_id)
        self.assertFalse(reconciled.untracked_order)
        self.assertEqual(reconciled.position, "long")
        self.assertEqual(reconciled.position_volume, "0.1")
        self.assertEqual(load_state(self.state_path), reconciled)

    @patch("bithumb_coin_trader.execution.time.sleep", return_value=None)
    def test_reconcile_until_terminal_polls_without_resubmission(self, _sleep: object) -> None:
        active = self.state(
            active_client_order_id="intent-gated",
            pending_order_side="bid",
            pending_market="KRW-BTC",
            untracked_order=False,
        )
        save_state(self.state_path, active)
        client = SequentialOrderClient(
            [order_result("intent-gated", "wait"), order_result("intent-gated", "done")]
        )
        reconciled = BithumbExecutor(client, state_path=self.state_path).reconcile_until_terminal()
        self.assertEqual(reconciled.position, "long")
        self.assertEqual([name for name, _ in client.calls], ["trade_get_order", "trade_get_order"])

    @patch("bithumb_coin_trader.execution.time.sleep", return_value=None)
    @patch("bithumb_coin_trader.execution.time.monotonic", side_effect=[0.0, 2.0])
    def test_reconcile_timeout_preserves_pending_state(
        self, _clock: object, _sleep: object
    ) -> None:
        active = self.state(
            active_client_order_id="intent-gated",
            pending_order_side="bid",
            pending_market="KRW-BTC",
            untracked_order=True,
        )
        save_state(self.state_path, active)
        client = SequentialOrderClient([order_result("intent-gated", "wait")])
        with self.assertRaisesRegex(ExecutionError, "remained pending"):
            BithumbExecutor(client, state_path=self.state_path).reconcile_until_terminal(
                timeout_seconds=1.0
            )
        self.assertEqual(load_state(self.state_path), active)

    def test_fill_ledger_is_persisted_before_pending_state_is_cleared(self) -> None:
        active = self.state(
            active_client_order_id="intent-gated",
            pending_order_side="bid",
            pending_market="KRW-BTC",
        )
        save_state(self.state_path, active)
        payload = json.loads(order_result("intent-gated", "done")["content"][0]["text"])
        payload.update(
            {
                "uuid": "order-1",
                "paid_fee": "4",
                "trades": [
                    {
                        "uuid": "trade-1",
                        "market": "KRW-BTC",
                        "side": "bid",
                        "price": "100000",
                        "volume": "0.1",
                        "funds": "10000",
                        "created_at": "2026-08-24T14:00:00+09:00",
                    }
                ],
            }
        )
        client = FakeClient(order={"content": [{"type": "text", "text": json.dumps(payload)}]})
        ledger = FillLedger(Path(self.temporary_directory.name) / "fills.jsonl")
        reconciled = BithumbExecutor(
            client, state_path=self.state_path, fill_ledger=ledger
        ).reconcile_active_order()
        self.assertIsNone(reconciled.active_client_order_id)
        self.assertEqual(ledger.position("KRW-BTC").volume, Decimal("0.1"))

    def test_versioned_sell_recovery_rejects_unrelated_closed_history(self) -> None:
        ledger = FillLedger(Path(self.temporary_directory.name) / "fills.jsonl")
        ledger.append_order({
            "uuid": "old-buy", "market": "KRW-BTC", "side": "bid", "paid_fee": "1",
            "trades": [{
                "uuid": "old-buy-fill", "market": "KRW-BTC", "side": "bid",
                "price": "100000", "volume": "0.1", "funds": "10000",
                "created_at": "2026-08-20T10:00:00+09:00",
            }],
        })
        ledger.append_order({
            "uuid": "old-sell", "market": "KRW-BTC", "side": "ask", "paid_fee": "1",
            "trades": [{
                "uuid": "old-sell-fill", "market": "KRW-BTC", "side": "ask",
                "price": "101000", "volume": "0.1", "funds": "10100",
                "created_at": "2026-08-20T11:00:00+09:00",
            }],
        })
        active = BotState(
            position="long", position_volume="0.1",
            active_client_order_id="current-sell", pending_order_side="ask",
            pending_market="KRW-BTC", pending_order_volume="0.1",
            position_policy_version=1,
        )
        save_state(self.state_path, active)
        payload = {
            "uuid": "current-sell-order", "client_order_id": "current-sell",
            "market": "KRW-BTC", "side": "ask", "state": "done",
            "executed_volume": "0.1", "paid_fee": "1",
            "trades": [{
                "uuid": "current-sell-fill", "market": "KRW-BTC", "side": "ask",
                "price": "102000", "volume": "0.1", "funds": "10200",
                "created_at": "2026-08-24T15:00:00+09:00",
            }],
        }
        client = FakeClient(
            order={"content": [{"type": "text", "text": json.dumps(payload)}]}
        )
        with self.assertRaisesRegex(FillLedgerError, "exceeds tracked position"):
            BithumbExecutor(
                client,
                state_path=self.state_path,
                fill_ledger=ledger,
            ).reconcile_active_order()
        self.assertEqual(load_state(self.state_path), active)
        self.assertNotIn("current-sell-fill", ledger.path.read_text(encoding="utf-8"))

    def test_reconcile_filled_sell_updates_position_before_clearing(self) -> None:
        active = BotState(
            position="long",
            position_volume="0.1",
            active_client_order_id="sell-safe",
            pending_order_side="ask",
            pending_market="KRW-BTC",
            untracked_order=True,
        )
        save_state(self.state_path, active)
        client = FakeClient(
            order=order_result(
                "sell-safe", "done", side="ask", executed_volume="0.1"
            )
        )
        reconciled = BithumbExecutor(
            client, state_path=self.state_path
        ).reconcile_active_order()
        self.assertEqual(reconciled.position, "flat")
        self.assertEqual(reconciled.position_volume, "0")
        self.assertIsNone(reconciled.active_client_order_id)
        self.assertIsNone(reconciled.pending_order_side)

    def test_reconcile_partial_cancel_preserves_authoritative_remainder(self) -> None:
        cases = (
            (
                self.state(
                    active_client_order_id="partial-buy",
                    pending_order_side="bid",
                    pending_market="KRW-BTC",
                    untracked_order=True,
                ),
                order_result("partial-buy", "cancel", executed_volume="0.03"),
                "0.03",
            ),
            (
                BotState(
                    position="long",
                    position_volume="0.1",
                    active_client_order_id="partial-sell",
                    pending_order_side="ask",
                    pending_market="KRW-BTC",
                    untracked_order=True,
                ),
                order_result(
                    "partial-sell", "cancel", side="ask", executed_volume="0.04"
                ),
                "0.06",
            ),
        )
        for active, response, expected_volume in cases:
            save_state(self.state_path, active)
            reconciled = BithumbExecutor(
                FakeClient(order=response), state_path=self.state_path
            ).reconcile_active_order()
            self.assertEqual(reconciled.position, "long")
            self.assertEqual(reconciled.position_volume, expected_volume)
            self.assertIsNone(reconciled.active_client_order_id)
            self.assertFalse(reconciled.untracked_order)

    def test_reconcile_done_sell_requires_full_tracked_volume(self) -> None:
        active = BotState(
            position="long",
            position_volume="0.1",
            active_client_order_id="short-fill",
            pending_order_side="ask",
            pending_market="KRW-BTC",
            untracked_order=True,
        )
        save_state(self.state_path, active)
        client = FakeClient(
            order=order_result(
                "short-fill", "done", side="ask", executed_volume="0.04"
            )
        )
        with self.assertRaisesRegex(ExecutionError, "full tracked position"):
            BithumbExecutor(
                client, state_path=self.state_path
            ).reconcile_active_order()
        self.assertEqual(load_state(self.state_path), active)

    def test_reconcile_done_intentional_partial_sell_preserves_remainder(self) -> None:
        active = BotState(
            position="long",
            position_volume="0.1",
            active_client_order_id="partial-tp",
            pending_order_side="ask",
            pending_market="KRW-BTC",
            pending_order_volume="0.04",
        )
        save_state(self.state_path, active)
        reconciled = BithumbExecutor(
            FakeClient(
                order=order_result(
                    "partial-tp", "done", side="ask", executed_volume="0.04"
                )
            ),
            state_path=self.state_path,
        ).reconcile_active_order()
        self.assertEqual(reconciled.position, "long")
        self.assertEqual(reconciled.position_volume, "0.06")
        self.assertIsNone(reconciled.pending_order_volume)

    def test_partial_exit_cannot_leave_unsellable_remainder(self) -> None:
        state = BotState(
            position="long",
            position_volume="0.1",
            position_policy_version=1,
        )
        save_state(self.state_path, state)
        plan = plan_execution(
            TradeIntent("KRW-BTC", "FLAT", base_volume="0.099"),
            "LONG",
            client_order_id="dust-blocked",
            allow_partial_exit=True,
        )
        with self.assertRaisesRegex(RiskRejectedError, "below the minimum"):
            BithumbExecutor(
                self.client,
                state_path=self.state_path,
                settings=self.live_settings(),
                env=self.live_env(),
            ).execute(
                plan,
                risk_context=self.risk_context(
                    requested_side=Signal.FLAT,
                    requested_notional_krw=9_900,
                    reference_price_krw=100_000,
                ),
                bot_state=state,
                confirmation_token=LIVE_CONFIRMATION_TOKEN,
            )
        self.assertEqual(self.client.calls, [])

    def test_reconcile_unknown_or_mismatched_status_fails_closed(self) -> None:
        active = self.state(
            active_client_order_id="intent-gated",
            pending_order_side="bid",
            pending_market="KRW-BTC",
            untracked_order=True,
        )
        for response in (
            order_result("different-id", "done"),
            order_result("intent-gated", "mystery"),
            {"content": [{"type": "text", "text": "{}"}]},
        ):
            save_state(self.state_path, active)
            client = FakeClient(order=response)
            with self.subTest(response=response), self.assertRaises(ExecutionError):
                BithumbExecutor(
                    client, state_path=self.state_path
                ).reconcile_active_order()
            self.assertEqual(load_state(self.state_path), active)
            self.assertEqual(len(client.calls), 1)


class ReadSafetyTests(unittest.TestCase):
    def test_default_server_is_read_only_and_live_command_is_explicit(self) -> None:
        self.assertIn("@bithumb-official/bithumb-mcp@0.8.5", DEFAULT_COMMAND)
        self.assertEqual(DEFAULT_COMMAND[DEFAULT_COMMAND.index("--modules") + 1], "account")
        self.assertEqual(DEFAULT_COMMAND[-1], "--read-only")
        self.assertEqual(
            LIVE_COMMAND[LIVE_COMMAND.index("--modules") + 1],
            "market,account,trade",
        )

    def test_child_environment_is_minimal(self) -> None:
        child_env = minimal_child_env({"BITHUMB_ACCESS_KEY": "test-key"})
        self.assertLessEqual(set(child_env), ALLOWED_CHILD_ENV)
        self.assertIn("PATH", child_env)
        self.assertIn("HOME", child_env)
        self.assertNotIn("BITHUMB_LIVE_TRADING", child_env)
        with self.assertRaises(ValueError):
            minimal_child_env({"UNSAFE_SECRET": "value"})

    def test_read_boundary_allows_query_and_blocks_order(self) -> None:
        client = FakeReadClient()
        client.call_read_tool("account_get_order_chance", {"market": "KRW-BTC"})
        self.assertEqual(len(client.calls), 1)
        with self.assertRaises(UnsafeToolError):
            client.call_read_tool("trade_place_order", {"market": "KRW-BTC"})
        self.assertEqual(len(client.calls), 1)

    def test_write_boundary_allows_only_single_order_tool(self) -> None:
        client = FakeReadClient()
        client.call_tool("trade_place_order", {"market": "KRW-BTC"})
        with self.assertRaises(UnsafeToolError):
            client.call_tool("trade_cancel_order", {})


if __name__ == "__main__":
    unittest.main()
