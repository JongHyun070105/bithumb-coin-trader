from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import time


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def append(path: Path, event: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(event + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("collector", "publisher"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--metrics", type=Path)
    parser.add_argument("--lifecycle", type=Path)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args()
    if args.mode == "publisher":
        append(args.events, "publisher-start")
        time.sleep(args.sleep)
        append(args.events, "publisher-stop")
        return args.exit_code

    assert args.metrics is not None and args.lifecycle is not None
    received: list[int] = []

    def stop(signum: int, _frame: object) -> None:
        received.append(signum)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    append(args.events, "collector-start")
    atomic_json(
        args.metrics,
        {
            "schema_version": 1,
            "collector_run_id": args.run_id,
            "process_id": os.getpid(),
            "written_at": datetime.now(timezone.utc).isoformat(),
            "active_partition_files": ["current.jsonl"],
            "unpersisted_event_count": 0,
            "exchanges": {},
        },
    )
    deadline = time.monotonic() + args.sleep
    while time.monotonic() < deadline and not received:
        time.sleep(0.01)
    if received:
        append(args.events, signal.Signals(received[0]).name)
    append(args.events, "writer-drain")
    atomic_json(
        args.metrics,
        {
            "schema_version": 1,
            "collector_run_id": args.run_id,
            "process_id": os.getpid(),
            "written_at": datetime.now(timezone.utc).isoformat(),
            "active_partition_files": [],
            "unpersisted_event_count": 0,
            "exchanges": {},
        },
    )
    append(args.events, "final-metrics")
    atomic_json(
        args.lifecycle,
        {
            "schema_version": 1,
            "collector_run_id": args.run_id,
            "final_manifest_flush_observed": True,
            "manifest_count": 1,
        },
    )
    append(args.events, "final-manifest")
    return args.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
