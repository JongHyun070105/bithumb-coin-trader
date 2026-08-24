#!/usr/bin/env python3
"""Run the sealed-holdout selective win-rate research comparison."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping, Sequence, Any

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.winrate_research import build_report


DEFAULT_INPUT = Path("data/krw-btc-30m-2026-08-24-winrate.csv")
DEFAULT_OUTPUT = Path(".omx/specs/autoresearch-winrate70/result.json")
DEFAULT_REPORT = Path("reports/krw-btc-winrate70-research-2026-08-24.json")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOLDOUT_LEDGER = (
    REPOSITORY_ROOT
    / ".omx/specs/autoresearch-winrate70/holdout-ledger.json"
)


class HoldoutLedgerExistsError(RuntimeError):
    """Raised when the one-time holdout has already started opening."""


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(payload))


def _mapping_sha256(payload: Mapping[str, Any]) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _create_opening_ledger(
    path: Path, development_report: Mapping[str, Any], finalists: Sequence[str]
) -> dict[str, Any]:
    """Exclusively reserve the holdout before any holdout metric is computed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = {
        "schema_version": 1,
        "state": "opening",
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": development_report["dataset"]["sha256"],
        "candidate_manifest_sha256": development_report["candidate_manifest"]["sha256"],
        "protocol_sha256": _mapping_sha256(development_report["protocol"]),
        "finalists": list(finalists),
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise HoldoutLedgerExistsError(
            f"holdout evaluation refused: ledger already exists at {path}"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_json_bytes(ledger))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        # Intentionally retain even a partial/crash-state ledger. Its existence is
        # the fail-closed signal that prevents accidental holdout reuse.
        raise
    return ledger


def _mark_ledger_opened(
    path: Path,
    opening_ledger: Mapping[str, Any],
    report: Mapping[str, Any],
) -> None:
    ledger = dict(opening_ledger)
    ledger.update(
        {
            "state": "opened",
            "opened_at": datetime.now(UTC).isoformat(),
            "report_sha256": sha256(_json_bytes(report)).hexdigest(),
            "evaluated_candidates": list(
                report["sealed_holdout"]["evaluated_candidates"]
            ),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(_json_bytes(ledger))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--open-holdout",
        action="store_true",
        help="irreversibly evaluate the sealed holdout for development finalists",
    )
    args = parser.parse_args(argv)
    ledger_path = DEFAULT_HOLDOUT_LEDGER
    if os.path.lexists(ledger_path):
        raise HoldoutLedgerExistsError(
            "research run refused because the one-time holdout ledger already "
            f"exists at {ledger_path}"
        )
    candles = load_candles_csv(args.input)
    result = build_report(candles, evaluate_holdout=False)
    passed = list(result["development"]["passed_candidates"])
    maximum = int(result["protocol"]["sealed_holdout"]["maximum_candidates"])
    finalists = passed[:maximum]

    if args.open_holdout and finalists:
        opening_ledger = _create_opening_ledger(
            ledger_path, result, finalists
        )
        generated_at = datetime.fromisoformat(str(result["generated_at"]))
        result = build_report(
            candles,
            generated_at=generated_at,
            evaluate_holdout=True,
        )
        evaluated = list(result["sealed_holdout"]["evaluated_candidates"])
        if evaluated != finalists:
            raise RuntimeError(
                "holdout evaluation candidate set differs from the reserved finalists"
            )
        _mark_ledger_opened(ledger_path, opening_ledger, result)

    _write_json(args.output, result)
    _write_json(args.report, result)
    selection = result["selection"]
    print(f"research artifact: {args.output}")
    print(
        "candidate: "
        f"{selection['research_candidate']}; "
        f"historical target met: {selection['historical_target_met']}; "
        "automatic promotion: forbidden"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
