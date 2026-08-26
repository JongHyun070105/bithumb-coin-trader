"""Cumulative research trial ledger and Deflated Sharpe Ratio (DSR) accounting.

Every historical backtest trial (V1 through V5+) is recorded here with code/dataset
hashes, parameter fingerprints, and observed performance metrics to prevent
data-snooping bias and calculate honest DSR probabilities.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Sequence

from .research_statistics import DeflatedSharpeResult, deflated_sharpe_ratio

DEFAULT_LEDGER_PATH = Path(__file__).resolve().parents[2] / "reports" / "research_trial_ledger.jsonl"


@dataclass(frozen=True, slots=True)
class TrialRecord:
    """Immutable record of a backtest trial."""

    trial_id: str
    lane: str  # e.g., "V1", "V2", "V3", "V4", "V5"
    strategy_name: str
    parameters: dict[str, Any]
    dataset_manifest_sha256: str
    code_hash: str
    created_at: str
    total_return: float
    maximum_drawdown: float
    observed_sharpe: float
    exposure: float
    description: str


def append_trial_record(record: TrialRecord, *, ledger_path: Path = DEFAULT_LEDGER_PATH) -> None:
    """Append a trial record to the permanent ledger file."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def load_trial_ledger(*, ledger_path: Path = DEFAULT_LEDGER_PATH) -> list[TrialRecord]:
    """Load all historical trial records from the ledger."""
    if not ledger_path.exists():
        return []
    records: list[TrialRecord] = []
    with ledger_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            records.append(TrialRecord(**data))
    return records


def calculate_ledger_dsr(
    candidate_returns: Sequence[float],
    *,
    candidate_sharpe: float | None = None,
    ledger_records: Sequence[TrialRecord] | None = None,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> DeflatedSharpeResult:
    """Calculate Deflated Sharpe Ratio using all historical trial Sharpes in the ledger.

    Converts annualized historical Sharpes to daily matching the per-period return frequency.
    """
    from math import sqrt
    records = ledger_records if ledger_records is not None else load_trial_ledger(ledger_path=ledger_path)

    # Collect all historical annualized Sharpes and convert to daily
    ann_factor = sqrt(365.25)
    annual_sharpes = [r.observed_sharpe for r in records if r.observed_sharpe is not None]

    if candidate_sharpe is not None and candidate_sharpe not in annual_sharpes:
        annual_sharpes.append(candidate_sharpe)

    if not annual_sharpes:
        annual_sharpes = [0.0]

    daily_sharpes = [s / ann_factor for s in annual_sharpes]
    trial_count = max(len(daily_sharpes), len(records), 1)

    return deflated_sharpe_ratio(
        candidate_returns,
        trial_sharpes=daily_sharpes,
        trial_count=trial_count,
    )
