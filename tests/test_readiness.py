from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.readiness import PaperEvidence, assess_live_readiness
from bithumb_coin_trader.state import BotState


ACCESS_SECRET = "access-super-secret-value"
SIGNING_SECRET = "signing-super-secret-value"


class FakeProbe:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, str]]] = []

    def call_read_tool(self, name: str, arguments: dict[str, str]) -> object:
        self.calls.append((name, arguments))
        return self.result


def chance_result(*, nested: bool = False, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
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
        "bid_account": {"currency": "KRW", "balance": "10000"},
        "ask_account": {"currency": "BTC", "balance": "0"},
    }
    payload.update(overrides)
    response_payload: object = {"data": {"data": payload}} if nested else payload
    return {"content": [{"type": "text", "text": json.dumps(response_payload)}]}


class ReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        observed = datetime(2026, 8, 11, tzinfo=UTC)
        self.paper = PaperEvidence(
            started_at=observed - timedelta(days=30),
            observed_at=observed,
            decision_count=100,
            completed_round_trips=30,
            accounting_mismatches=0,
        )
        self.env = {
            "BITHUMB_DISCORD_TARGET": "discord:123456789",
            "BITHUMB_ACCESS_KEY": ACCESS_SECRET,
            "BITHUMB_SECRET_KEY": SIGNING_SECRET,
            "TRADING_MODE": "paper",
            "BITHUMB_LIVE_TRADING": "false",
        }

    def assess(self, **overrides: object):
        values = {
            "research_report": {"promotion": {"status": "PAPER_CANDIDATE"}},
            "paper": self.paper,
            "bot_state": BotState(),
            "env": self.env,
        }
        values.update(overrides)
        return assess_live_readiness(**values)

    def test_missing_mcp_probe_can_never_be_ready(self) -> None:
        report = self.assess()
        self.assertFalse(report.ready)
        self.assertFalse(report.checks[-1].passed)

    def test_each_required_evidence_fails_closed(self) -> None:
        cases = (
            {"research_report": {"promotion": "RESEARCH_ONLY"}},
            {
                "paper": PaperEvidence(
                    self.paper.started_at + timedelta(days=1),
                    self.paper.observed_at,
                    100,
                    30,
                    0,
                )
            },
            {"paper": PaperEvidence(self.paper.started_at, self.paper.observed_at, 99, 30, 0)},
            {"paper": PaperEvidence(self.paper.started_at, self.paper.observed_at, 100, 29, 0)},
            {"paper": PaperEvidence(self.paper.started_at, self.paper.observed_at, 100, 30, 1)},
            {"bot_state": BotState(untracked_order=True)},
            {"bot_state": None},
            {"env": {**self.env, "BITHUMB_DISCORD_TARGET": "not-discord"}},
            {"env": {key: value for key, value in self.env.items() if key != "BITHUMB_SECRET_KEY"}},
            {"env": {**self.env, "BITHUMB_LIVE_TRADING": "true"}},
            {"env": {**self.env, "TRADING_MODE": "live"}},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertEqual(self.assess(**overrides).status, "NOT_READY")

    def test_report_never_contains_secret_values(self) -> None:
        serialized = json.dumps(self.assess().as_dict(), sort_keys=True)
        self.assertNotIn(ACCESS_SECRET, serialized)
        self.assertNotIn(SIGNING_SECRET, serialized)
        self.assertIn("BITHUMB_ACCESS_KEY", json.dumps(
            self.assess(env={"BITHUMB_SECRET_KEY": SIGNING_SECRET}).as_dict()
        ))

    def test_optional_mcp_probe_is_read_only_and_sanitized(self) -> None:
        probe = FakeProbe(chance_result(nested=True))
        report = self.assess(mcp_probe=probe)
        self.assertTrue(report.ready)
        self.assertEqual(
            probe.calls,
            [("account_get_order_chance", {"market": "KRW-BTC"})],
        )

    def test_malformed_or_insufficient_probe_is_not_ready(self) -> None:
        cases = (
            {},
            {"content": [{"type": "text", "text": "not-json"}]},
            chance_result(bid_fee="NaN"),
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"data": {"data": {"data": {}}}}),
                    }
                ]
            },
            chance_result(
                market={
                    "id": "KRW-ETH",
                    "bid": {"currency": "KRW", "min_total": "5000"},
                    "ask": {"currency": "ETH", "min_total": "5000"},
                }
            ),
            chance_result(bid_account={"currency": "KRW", "balance": "100"}),
        )
        for result in cases:
            probe = FakeProbe(result)
            with self.subTest(result=result):
                report = self.assess(mcp_probe=probe)
                self.assertEqual(report.status, "NOT_READY")
                self.assertEqual(len(probe.calls), 1)


if __name__ == "__main__":
    unittest.main()
