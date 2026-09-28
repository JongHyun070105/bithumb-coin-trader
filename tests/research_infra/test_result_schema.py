from __future__ import annotations

import pytest

from bithumb_coin_trader.research_infra.result_schema import (
    ExperimentResult,
    RESULT_SCHEMA_VERSION,
    ResultStatus,
)


def _result(**overrides: object) -> ExperimentResult:
    values: dict[str, object] = {
        "experiment_id": "exp_123",
        "candidate_family": "core",
        "strategy_id": "strategy-a",
        "dataset_id": "synthetic",
        "code_revision": "a" * 40,
        "period": {"start_utc": "2024-01-01T00:00:00+00:00", "end_utc": "2024-01-02T00:00:00+00:00"},
        "fold": 0,
        "cost_scenario": "base",
        "status": ResultStatus.COMPLETED,
        "net_return": 0.05,
        "mdd": 0.02,
        "trade_count": 1,
        "fees": 10.0,
        "slippage_cost": 5.0,
    }
    values.update(overrides)
    return ExperimentResult(**values)  # type: ignore[arg-type]


def test_result_contract_round_trips_with_version_and_nullable_metrics() -> None:
    result = _result()

    serialized = result.to_dict()
    restored = ExperimentResult.from_dict(serialized)

    assert serialized["schema_version"] == RESULT_SCHEMA_VERSION
    assert serialized["gross_return"] is None
    assert serialized["profit_factor"] is None
    assert restored == result


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"status": ResultStatus.UNSUPPORTED}, "requires failure_reason"),
        ({"mdd": 1.1}, "mdd must be in"),
        ({"fees": float("nan")}, "fees must be finite"),
        ({"trade_count": -1}, "trade_count must be"),
    ],
)
def test_result_contract_rejects_invalid_or_unexplained_values(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _result(**overrides)


def test_result_contract_rejects_unknown_schema_version_and_fields() -> None:
    payload = _result().to_dict()
    payload["schema_version"] = 2
    with pytest.raises(ValueError, match="unsupported"):
        ExperimentResult.from_dict(payload)

    payload = _result().to_dict()
    payload["hidden_metric"] = 1.0
    with pytest.raises(ValueError, match="unknown"):
        ExperimentResult.from_dict(payload)
