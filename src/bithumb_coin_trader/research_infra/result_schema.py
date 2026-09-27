"""Versioned machine-readable contract for governed experiment outcomes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import math
from typing import Any, Mapping


RESULT_SCHEMA_VERSION = 1


class ResultStatus(StrEnum):
    COMPLETED = "COMPLETED"
    UNSUPPORTED = "UNSUPPORTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    experiment_id: str
    candidate_family: str
    strategy_id: str
    dataset_id: str
    code_revision: str
    period: dict[str, str]
    fold: int | str
    cost_scenario: str
    status: ResultStatus
    failure_reason: str | None = None

    gross_return: float | None = None
    net_return: float | None = None
    cagr: float | None = None
    mdd: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    calmar: float | None = None
    profit_factor: float | None = None
    expectancy: float | None = None
    win_rate: float | None = None
    trade_count: int | None = None
    turnover: float | None = None
    exposure: float | None = None
    fees: float | None = None
    slippage_cost: float | None = None
    worst_day: float | None = None
    worst_week: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "experiment_id", "candidate_family", "strategy_id", "dataset_id",
            "code_revision", "cost_scenario",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if set(self.period) != {"start_utc", "end_utc"} or any(
            not isinstance(value, str) or not value for value in self.period.values()
        ):
            raise ValueError("period must contain non-empty start_utc and end_utc strings")
        if isinstance(self.fold, bool) or not isinstance(self.fold, (int, str)):
            raise ValueError("fold must be an integer or non-empty string")
        if isinstance(self.fold, str) and not self.fold.strip():
            raise ValueError("fold string must be non-empty")
        try:
            status = ResultStatus(self.status)
        except ValueError as exc:
            raise ValueError(f"unknown result status: {self.status}") from exc
        object.__setattr__(self, "status", status)
        if status in {ResultStatus.FAILED, ResultStatus.UNSUPPORTED}:
            if not isinstance(self.failure_reason, str) or not self.failure_reason.strip():
                raise ValueError(f"{status.value} result requires failure_reason")
        for name in (
            "gross_return", "net_return", "cagr", "mdd", "sharpe", "sortino",
            "calmar", "profit_factor", "expectancy", "win_rate", "turnover",
            "exposure", "fees", "slippage_cost", "worst_day", "worst_week",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be finite or null")
        if self.trade_count is not None and (
            isinstance(self.trade_count, bool)
            or not isinstance(self.trade_count, int)
            or self.trade_count < 0
        ):
            raise ValueError("trade_count must be a non-negative integer or null")
        if self.mdd is not None and not 0.0 <= self.mdd <= 1.0:
            raise ValueError("mdd must be in [0, 1]")
        if self.win_rate is not None and not 0.0 <= self.win_rate <= 1.0:
            raise ValueError("win_rate must be in [0, 1]")
        if self.exposure is not None and not 0.0 <= self.exposure <= 1.0:
            raise ValueError("exposure must be in [0, 1]")
        if self.turnover is not None and self.turnover < 0.0:
            raise ValueError("turnover must be non-negative")
        if self.fees is not None and self.fees < 0.0:
            raise ValueError("fees must be non-negative")
        if self.slippage_cost is not None and self.slippage_cost < 0.0:
            raise ValueError("slippage_cost must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["schema_version"] = RESULT_SCHEMA_VERSION
        result["status"] = self.status.value
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExperimentResult":
        if value.get("schema_version") != RESULT_SCHEMA_VERSION:
            raise ValueError("unsupported experiment result schema version")
        fields = set(cls.__dataclass_fields__)
        unknown = set(value) - fields - {"schema_version"}
        missing = fields - value.keys()
        if unknown:
            raise ValueError("unknown experiment result fields: " + ", ".join(sorted(unknown)))
        if missing:
            raise ValueError("missing experiment result fields: " + ", ".join(sorted(missing)))
        payload = dict(value)
        payload.pop("schema_version", None)
        payload["status"] = ResultStatus(payload["status"])
        return cls(**payload)
