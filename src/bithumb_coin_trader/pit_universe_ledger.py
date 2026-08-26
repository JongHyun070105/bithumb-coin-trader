"""Point-in-Time Dynamic Liquid Universe Ledger.

Records exact machine-readable snapshots of eligible/selected universe markets at timestamp T.
Eliminates all survivorship and lookahead bias for Strategy V9 research.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_LEDGER_DIR = ROOT / "data" / "microstructure" / "universe_ledger"


@dataclass(frozen=True, slots=True)
class UniverseSnapshotRecord:
    timestamp: str
    selected_top_n: int
    selected_markets: tuple[str, ...]
    candidate_pool_size: int
    selection_metric: str
    warning_exclusions: tuple[str, ...]
    suspension_exclusions: tuple[str, ...]
    registry_version: str = "v9.0.0-pit"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PointInTimeUniverseLedger:
    """Manages immutable Point-in-Time universe snapshot ledger files."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or UNIVERSE_LEDGER_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_file = self.base_dir / "pit_universe_history.jsonl"

    def record_universe_snapshot(
        self,
        timestamp: datetime,
        selected_markets: Sequence[str],
        candidate_pool: Sequence[str],
        warning_exclusions: Sequence[str] = (),
        suspension_exclusions: Sequence[str] = (),
        selection_metric: str = "30d_rolling_trade_value_krw",
    ) -> UniverseSnapshotRecord:
        record = UniverseSnapshotRecord(
            timestamp=timestamp.astimezone(timezone.utc).isoformat(),
            selected_top_n=len(selected_markets),
            selected_markets=tuple(selected_markets),
            candidate_pool_size=len(candidate_pool),
            selection_metric=selection_metric,
            warning_exclusions=tuple(warning_exclusions),
            suspension_exclusions=tuple(suspension_exclusions),
        )
        line = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
        with self.ledger_file.open("a", encoding="utf-8") as f:
            f.write(line)
        return record

    def get_latest_universe(self, as_of: datetime | None = None) -> tuple[str, ...] | None:
        if not self.ledger_file.exists():
            return None
        last_match: tuple[str, ...] | None = None
        target_iso = as_of.astimezone(timezone.utc).isoformat() if as_of else None

        with self.ledger_file.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                d = json.loads(line)
                rec_ts = d.get("timestamp")
                if target_iso is None or (rec_ts and rec_ts <= target_iso):
                    last_match = tuple(d.get("selected_markets", []))
        return last_match
