from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import pytest
import zstandard

from bithumb_coin_trader.research_infra.dataset_scan import (
    compare_scan_reports, scan_dataset, scan_twice, ts_to_ms, write_report,
)

T0 = 1_790_000_000_000


def rec(market="KRW-BTC", stream="trade", exch="bithumb", recv=T0, write=T0 + 1, payload=None, ex_ts=None):
    return {"exchange": exch, "stream": stream, "market": market, "exchange_ts": ex_ts if ex_ts is not None else recv,
            "local_recv_ts": recv, "local_write_ts": write, "payload": payload if payload is not None else {"p": recv}}


def write_jsonl(path: Path, rows, terminated=True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r) for r in rows) + ("\n" if terminated else "")
    path.write_text(text)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "ds"
    write_jsonl(r / "a/h1.jsonl", [rec(recv=T0 + i, write=T0 + i + 1) for i in range(5)])
    write_jsonl(r / "a/h2.jsonl", [rec(market="KRW-ETH", stream="orderbook", recv=T0 + 100 + i, write=T0 + 101 + i) for i in range(3)])
    return r


FEEDS = ["bithumb:trade:KRW-BTC", "bithumb:orderbook:KRW-ETH"]


def scan(root: Path, **kw):
    return scan_dataset(root, dataset_label="t", expected_feeds=kw.pop("expected_feeds", FEEDS), **kw)


def test_clean_scan_counts_and_never_qualifies(root: Path) -> None:
    rep = scan(root)
    assert rep["scan_status"] == "SCAN_CLEAN"
    assert rep["totals"]["files"] == 2 and rep["totals"]["rows"] == 8
    assert rep["dataset_qualified"] is False and rep["qualification_authority"] == "NONE"
    assert rep["missing_feeds"] == [] and rep["unexpected_feeds"] == []


def test_report_is_deterministic_and_has_no_absolute_paths(root: Path) -> None:
    a, cmp = scan_twice(root, dataset_label="t", expected_feeds=FEEDS)
    assert cmp == {"identical": True, "differences": [], "differing_files": []}
    assert str(root) not in json.dumps(a)


def test_deterministic_across_copy_to_different_location(root: Path, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere" / "copy"
    shutil.copytree(root, other)
    assert scan(root)["manifest_sha256"] == scan(other)["manifest_sha256"]


def test_zero_byte_file_flagged(root: Path) -> None:
    (root / "a/empty.jsonl").write_bytes(b"")
    rep = scan(root)
    assert rep["scan_status"] == "SCAN_ISSUES" and "zero_byte" in rep["issue_files"]["a/empty.jsonl"]


def test_unterminated_final_line_flagged(root: Path) -> None:
    write_jsonl(root / "a/cut.jsonl", [rec(recv=T0 + 500)], terminated=False)
    assert "unterminated_final_line" in scan(root)["issue_files"]["a/cut.jsonl"]


def test_truncated_gzip_and_zstd_flagged(root: Path) -> None:
    body = ("\n".join(json.dumps(rec(recv=T0 + i, write=T0 + i + 1)) for i in range(2000)) + "\n").encode()
    gz = gzip.compress(body)
    (root / "a/t.jsonl.gz").write_bytes(gz[: len(gz) // 2])
    z = zstandard.ZstdCompressor().compress(body)
    (root / "a/t.jsonl.zst").write_bytes(z[: len(z) // 2])
    issues = scan(root)["issue_files"]
    assert "truncated" in issues["a/t.jsonl.gz"] and "truncated" in issues["a/t.jsonl.zst"]


def test_intact_compressed_files_scan_clean(root: Path) -> None:
    body = ("\n".join(json.dumps(rec(recv=T0 + 900 + i, write=T0 + 901 + i)) for i in range(10)) + "\n").encode()
    (root / "a/ok.jsonl.gz").write_bytes(gzip.compress(body))
    (root / "a/ok.jsonl.zst").write_bytes(zstandard.ZstdCompressor().compress(body.replace(b"T", b"T")))
    rep = scan(root)
    # same payloads in .gz and .zst are cross-file duplicate identities by design
    assert rep["issue_files"].get("a/ok.jsonl.gz") is None
    assert "duplicate_identity_rows" in rep["issue_files"]["a/ok.jsonl.zst"]


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("exchange"), lambda r: r.update(market=""), lambda r: r.update(stream=None),
    lambda r: r.update(payload=None), lambda r: r.pop("local_write_ts"), lambda r: r.update(local_recv_ts="garbage"),
    lambda r: r.update(local_recv_ts=True),
])
def test_null_or_missing_critical_fields_flagged(root: Path, mutate) -> None:
    bad = rec(recv=T0 + 700)
    mutate(bad)
    write_jsonl(root / "a/bad.jsonl", [bad])
    assert "null_critical_rows" in scan(root)["issue_files"]["a/bad.jsonl"]


def test_malformed_json_and_non_object_rows_flagged(root: Path) -> None:
    (root / "a/m.jsonl").write_text("{not json}\n[1,2]\n" + json.dumps(rec(recv=T0 + 800)) + "\n")
    assert next(f for f in scan(root)["files"] if f["path"] == "a/m.jsonl")["malformed_rows"] == 2
    assert "malformed_rows" in scan(root)["issue_files"]["a/m.jsonl"]


def test_timestamp_sanity(root: Path) -> None:
    write_jsonl(root / "a/ts.jsonl", [rec(recv=T0 + 600, write=T0 + 599), rec(recv=-5, write=-4, payload={"x": 1}), rec(recv=T0 + 10**12, write=T0 + 10**12 + 1, payload={"x": 2})])
    issues = scan(root, min_ts_ms=T0 - 1000, max_ts_ms=T0 + 10**6)["issue_files"]["a/ts.jsonl"]
    assert "write_before_recv_rows" in issues and "timestamp_violation_rows" in issues


def test_duplicate_identity_detected_across_files(root: Path) -> None:
    write_jsonl(root / "a/z_dup.jsonl", [rec(recv=T0, write=T0 + 1)])
    assert "duplicate_identity_rows" in scan(root)["issue_files"]["a/z_dup.jsonl"]


def test_local_write_time_alone_does_not_create_duplicates(root: Path) -> None:
    write_jsonl(root / "a/same_event_new_write.jsonl", [rec(recv=T0 + 40, write=T0 + 41, ex_ts=T0 + 3, payload={"p": T0 + 3})])
    write_jsonl(root / "a/z.jsonl", [rec(recv=T0 + 90, write=T0 + 91, ex_ts=T0 + 3, payload={"p": T0 + 3})])
    # identity ignores local timestamps by design: same exchange event twice is a duplicate
    assert "duplicate_identity_rows" in scan(root)["issue_files"]["a/z.jsonl"]


def test_missing_and_unexpected_feeds(root: Path) -> None:
    rep = scan(root, expected_feeds=FEEDS + ["upbit:trade:KRW-BTC"])
    assert rep["scan_status"] == "SCAN_ISSUES" and rep["missing_feeds"] == ["upbit:trade:KRW-BTC"]
    rep = scan(root, expected_feeds=FEEDS[:1])
    assert rep["unexpected_feeds"] == ["bithumb:orderbook:KRW-ETH"]


def test_empty_dataset_is_never_clean(tmp_path: Path) -> None:
    (tmp_path / "e").mkdir()
    assert scan_dataset(tmp_path / "e", dataset_label="t")["scan_status"] == "SCAN_ISSUES"


def test_missing_root_raises(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        scan_dataset(tmp_path / "nope", dataset_label="t")


def test_build_twice_detects_every_kind_of_drift(root: Path, tmp_path: Path) -> None:
    base = scan(root)
    mutated = tmp_path / "m"
    shutil.copytree(root, mutated)
    with open(mutated / "a/h1.jsonl", "a") as f:
        f.write(json.dumps(rec(recv=T0 + 9000, write=T0 + 9001)) + "\n")
    cmp = compare_scan_reports(base, scan(mutated))
    assert not cmp["identical"] and {"row_count", "file_hashes", "manifest_hash"} <= set(cmp["differences"]) and cmp["differing_files"] == ["a/h1.jsonl"]
    (mutated / "a/h1.jsonl").unlink()
    cmp = compare_scan_reports(base, scan(mutated))
    assert "file_count" in cmp["differences"] and "source_universe" in cmp["differences"]


def test_extra_non_raw_files_are_listed_but_not_scanned(root: Path) -> None:
    (root / "README.txt").write_text("x")
    rep = scan(root)
    assert rep["ignored_files"] == ["README.txt"] and rep["totals"]["files"] == 2


def test_write_report_roundtrip_hash(root: Path, tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    digest = write_report(scan(root), out)
    import hashlib
    assert hashlib.sha256(out.read_bytes()).hexdigest() == digest


def test_ts_to_ms() -> None:
    assert ts_to_ms(5) == 5
    assert ts_to_ms("2026-10-04T00:00:00Z") == 1791072000000
    assert ts_to_ms("2026-10-04T00:00:00+09:00") == 1791072000000 - 9 * 3600 * 1000
    for bad in (True, None, "x", "2026-10-04T00:00:00", float("nan"), float("inf")):
        assert ts_to_ms(bad) is None


def test_multi_frame_zstd_is_clean_and_truncated_second_frame_is_flagged(root: Path) -> None:
    def frame(i: int) -> bytes:
        return zstandard.ZstdCompressor().compress((json.dumps(rec(recv=T0 + 5000 + i, write=T0 + 5001 + i)) + "\n").encode() * 1)
    (root / "a/mf.jsonl.zst").write_bytes(frame(0) + frame(1))
    assert "a/mf.jsonl.zst" not in scan(root)["issue_files"]
    (root / "a/mf.jsonl.zst").write_bytes(frame(0) + frame(1)[:-3])
    assert "truncated" in scan(root)["issue_files"]["a/mf.jsonl.zst"]
