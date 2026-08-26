from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
from typing import Any, cast
import unittest

from scripts.validate_opportunity_research import _metadata_corrections
from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.opportunity_research import (
    OpportunityResearchConfig,
    HoldoutLedgerExistsError,
    _create_exclusive_reservation,
    _validate_holdout_reservation,
    build_report,
    classify_candidate,
    evaluate_holdout_gate,
)


def _metrics(**changes: float | int | bool | None) -> dict[str, float | int | bool | None]:
    result: dict[str, float | int | bool | None] = {
        "closed_trade_count": 25,
        "total_return": 0.05,
        "maximum_drawdown": 0.10,
        "profit_factor": 1.20,
        "profit_factor_is_infinite": False,
        "active_fold_count": 6,
        "positive_active_fold_count": 4,
        "maximum_single_win_contribution": 0.30,
        "bootstrap_probability_net_positive": 0.85,
    }
    result.update(changes)
    return result


def _candles(count: int, *, malformed: bool = False) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    candles = []
    for index in range(count):
        timestamp = start + timedelta(minutes=30 * index)
        if malformed and index == 10:
            timestamp += timedelta(minutes=4)
        candles.append(
            Candle(
                market="KRW-BTC",
                timestamp=timestamp,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=10.0,
            )
        )
    return candles


class OpportunityResearchTests(unittest.TestCase):
    def test_profit_first_gate_does_not_require_seventy_percent_win_rate(self) -> None:
        base = _metrics()
        stress = _metrics(total_return=0.02, profit_factor=1.05)
        result = classify_candidate(base, stress)
        self.assertEqual(result["status"], "FINALIST")

    def test_catastrophic_candidate_is_rejected_before_incubation(self) -> None:
        base = _metrics(total_return=-0.11, profit_factor=0.95)
        stress = _metrics(total_return=-0.14, profit_factor=0.90)
        self.assertEqual(classify_candidate(base, stress)["status"], "REJECTED")

    def test_report_drops_exchange_anomaly_and_keeps_holdout_sealed(self) -> None:
        config = OpportunityResearchConfig(
            historical_count=1_000,
            development_count=900,
            initial_train_count=300,
            development_test_count=100,
            development_fold_count=6,
            sealed_holdout_count=100,
            maximum_holdout_candidates=2,
        )
        report = build_report(_candles(1_001, malformed=True), config=config)
        self.assertEqual(report["dataset"]["candle_count"], 1_000)
        self.assertEqual(
            report["dataset"]["data_quality"]["rejected_non_aligned_count"], 1
        )
        self.assertIs(report["sealed_holdout"]["opened"], False)
        self.assertIs(report["selection"]["can_promote"], False)

    def test_public_report_builder_cannot_accept_fabricated_reservation(self) -> None:
        config = OpportunityResearchConfig(
            historical_count=1_000,
            development_count=900,
            initial_train_count=300,
            development_test_count=100,
            development_fold_count=6,
            sealed_holdout_count=100,
            maximum_holdout_candidates=2,
        )
        forged = {"state": "opening", "finalists": ["known-finalist"]}
        with self.assertRaises(TypeError):
            cast(Any, build_report)(
                _candles(1_000),
                config=config,
                evaluate_holdout=True,
                holdout_reservation=forged,
            )

    def test_reservation_rejects_hash_tampering(self) -> None:
        reservation = {
            "schema_version": 1,
            "state": "opening",
            "created_at": datetime.now(UTC).isoformat(),
            "dataset_sha256": "wrong",
            "holdout_sha256": "holdout",
            "candidate_manifest_sha256": "manifest",
            "protocol_sha256": "protocol",
            "finalists": ["candidate"],
        }
        with self.assertRaisesRegex(ValueError, "dataset_sha256"):
            _validate_holdout_reservation(
                reservation,
                dataset_sha256="dataset",
                holdout_sha256="holdout",
                candidate_manifest_sha256="manifest",
                protocol_sha256="protocol",
                finalists=["candidate"],
            )

    def test_existing_ledger_cannot_be_reopened(self) -> None:
        reservation = {
            "schema_version": 1,
            "state": "opening",
            "created_at": datetime.now(UTC).isoformat(),
            "dataset_sha256": "dataset",
            "holdout_sha256": "holdout",
            "candidate_manifest_sha256": "manifest",
            "protocol_sha256": "protocol",
            "finalists": ["candidate"],
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "holdout-ledger.json"
            _create_exclusive_reservation(ledger, reservation)
            with self.assertRaises(HoldoutLedgerExistsError):
                _create_exclusive_reservation(ledger, reservation)

    def test_forced_final_liquidation_cannot_pass_holdout(self) -> None:
        base = _metrics(
            forced_final_liquidation_count=1,
            bootstrap_probability_net_positive=0.90,
        )
        stress = _metrics(
            total_return=0.02,
            profit_factor=1.10,
            forced_final_liquidation_count=1,
            bootstrap_probability_net_positive=0.90,
        )
        result = evaluate_holdout_gate(base, stress)
        self.assertIs(result["passed"], False)

    def test_normal_opened_metadata_needs_no_correction(self) -> None:
        limitation = "The sealed 4,000-candle holdout was opened once for one finalist."
        actual = {
            "protocol": {"holdout": {"opened": True}},
            "limitations": ["a", "b", "c", "d", limitation],
        }
        expected_protocol = {"holdout": {"opened": True}}
        expected_limitations = ["a", "b", "c", "d", limitation]
        self.assertEqual(
            _metadata_corrections(actual, expected_protocol, expected_limitations),
            [],
        )

    def test_legacy_opened_metadata_requires_explicit_corrections(self) -> None:
        actual = {
            "protocol": {"holdout": {"opened": False}},
            "limitations": [
                "a",
                "b",
                "c",
                "d",
                "The sealed 4,000-candle holdout remains unopened.",
            ],
        }
        expected_protocol = {"holdout": {"opened": True}}
        expected_limitations = [
            "a",
            "b",
            "c",
            "d",
            "The sealed 4,000-candle holdout was opened once for one finalist.",
        ]
        corrections = _metadata_corrections(
            actual, expected_protocol, expected_limitations
        )
        self.assertEqual(
            [row["json_path"] for row in corrections],
            ["protocol.holdout.opened", "limitations[4]"],
        )
