from __future__ import annotations

import gzip
import json
import random
from pathlib import Path

import pytest

from bithumb_coin_trader.research_infra.canonical_builder import HOUR_MS, build_canonical, compare_builds

T0 = 1_790_000_000_000


def rec(i, market="KRW-BTC", stream="trade", recv=None, write=None):
    recv = T0 + i if recv is None else recv
    return {"exchange": "Bithumb", "stream": stream, "market": market, "exchange_ts": recv,
            "local_recv_ts": recv, "local_write_ts": recv + 1 if write is None else write, "payload": {"p": i}}


def put(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    r = tmp_path / "raw"
    rows = [rec(i) for i in range(30)] + [rec(i, market="krw-eth") for i in range(5)] + [rec(HOUR_MS * 2 + i) for i in range(4)]
    random.Random(1).shuffle(rows)
    put(r / "x/a.jsonl", rows[:20])
    put(r / "x/b.jsonl", rows[20:])
    return r


def test_build_partitions_sorts_normalizes_and_never_qualifies(raw: Path, tmp_path: Path) -> None:
    m = build_canonical(raw, tmp_path / "o1", dataset_label="t")
    assert m["dataset_qualified"] is False and m["qualification_authority"] == "NONE"
    assert m["total_rows"] == 39 and len(m["files"]) >= 2
    assert "bithumb:trade:KRW-ETH" in m["feeds"]
    for f in m["files"]:
        rows = [json.loads(line) for line in (tmp_path / "o1" / f["path"]).read_text().splitlines()]
        keys = [(r["local_write_ts_ms"], r["local_recv_ts_ms"]) for r in rows]
        assert keys == sorted(keys)


def test_two_independent_builds_are_identical_even_from_a_copy_and_input_order(raw: Path, tmp_path: Path) -> None:
    a = build_canonical(raw, tmp_path / "o1", dataset_label="t")
    b = build_canonical(raw, tmp_path / "o2", dataset_label="t")
    assert compare_builds(a, b) == {"identical": True, "differences": []}
    for f in a["files"]:
        assert (tmp_path / "o1" / f["path"]).read_bytes() == (tmp_path / "o2" / f["path"]).read_bytes()
    assert (tmp_path / "o1/MANIFEST.json").read_bytes() == (tmp_path / "o2/MANIFEST.json").read_bytes()


def test_row_order_within_input_files_does_not_change_output(raw: Path, tmp_path: Path) -> None:
    a = build_canonical(raw, tmp_path / "o1", dataset_label="t")
    lines = (raw / "x/a.jsonl").read_text().splitlines()
    (raw / "x/a.jsonl").write_text("\n".join(reversed(lines)) + "\n")
    b = build_canonical(raw, tmp_path / "o2", dataset_label="t")
    assert [f["sha256"] for f in a["files"]] == [f["sha256"] for f in b["files"]]


def test_duplicates_and_bad_rows_are_counted_not_silent(raw: Path, tmp_path: Path) -> None:
    put(raw / "z/dup.jsonl", [rec(1)])
    with open(raw / "z/bad.jsonl", "w") as f:
        f.write("{oops\n[1]\n" + json.dumps({"exchange": "x"}) + "\n")
    m = build_canonical(raw, tmp_path / "o", dataset_label="t")
    assert m["rejects"] == {"malformed": 2, "unusable_fields": 1, "duplicate": 1, "unreadable_files": 0}
    assert m["total_rows"] == 39


def test_truncated_gzip_counts_as_unreadable_file(raw: Path, tmp_path: Path) -> None:
    body = ("\n".join(json.dumps(rec(900 + i)) for i in range(2000)) + "\n").encode()
    gz = gzip.compress(body)
    (raw / "x/t.jsonl.gz").write_bytes(gz[: len(gz) // 2])
    assert build_canonical(raw, tmp_path / "o", dataset_label="t")["rejects"]["unreadable_files"] == 1


def test_source_mutation_is_detected_by_comparator(raw: Path, tmp_path: Path) -> None:
    a = build_canonical(raw, tmp_path / "o1", dataset_label="t")
    put(raw / "x/c.jsonl", [rec(12345)])
    b = build_canonical(raw, tmp_path / "o2", dataset_label="t")
    cmp = compare_builds(a, b)
    assert not cmp["identical"] and {"source_universe", "manifest_hash"} <= set(cmp["differences"])


def test_nonempty_output_root_refused_and_missing_raw_raises(raw: Path, tmp_path: Path) -> None:
    (tmp_path / "o").mkdir()
    (tmp_path / "o/keep").write_text("x")
    with pytest.raises(FileExistsError):
        build_canonical(raw, tmp_path / "o", dataset_label="t")
    with pytest.raises(NotADirectoryError):
        build_canonical(tmp_path / "nope", tmp_path / "o3", dataset_label="t")
