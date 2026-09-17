"""Independent hour-close completeness canary for closed UTC cohorts.

Monitors and audits all 76 sealed feeds for a closed UTC cohort after grace period.
Evaluates four expected artifacts:
1. RAW_DATA (or VERIFIED_ZERO)
2. COVERAGE_EVIDENCE
3. COMPRESSED_DATA (.jsonl.zst)
4. RECEIPT (cohort archive receipt)

Guarantees:
- Fail-Closed: Strictly validates integrity and terminal states.
- Non-Mutation Invariant: Never repairs, restarts, modifies, or fabricates data.
- V4 Failure Detection: Explicitly flags RAW 0/76 while coverage exists as HIGH_SEVERITY_OBSERVATION and FAIL.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from bithumb_coin_trader.archive_cohort import ArchiveCohortId
from bithumb_coin_trader.closed_hour_finalizer import SEALED_FEED_UNIVERSE
from bithumb_coin_trader.evidence_hashing import canonical_sha256, file_sha256
from bithumb_coin_trader.feed_hour_coverage import load_feed_hour_coverage
from bithumb_coin_trader.session_evidence import FeedIdentity


QUALIFYING_RECEIPT_STATES = frozenset({
    "CLEANUP_ELIGIBLE",
    "CLEANED",
    "RESTORE_VERIFIED",
})


class HourCloseCanaryError(Exception):
    """Base exception for hour-close canary operations."""
    pass


class CohortNotEligibleError(HourCloseCanaryError):
    """Raised when an hour cohort is inspected before its eligibility grace period expires."""
    pass


@dataclass(frozen=True)
class FeedCanaryStatus:
    feed: FeedIdentity
    raw_terminal: bool
    coverage_terminal: bool
    compressed_terminal: bool
    receipts_terminal: bool
    is_verified_zero: bool
    unknown_missing: bool
    binding_verified: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feed": self.feed.canonical,
            "raw_terminal": self.raw_terminal,
            "coverage_terminal": self.coverage_terminal,
            "compressed_terminal": self.compressed_terminal,
            "receipts_terminal": self.receipts_terminal,
            "is_verified_zero": self.is_verified_zero,
            "unknown_missing": self.unknown_missing,
            "binding_verified": self.binding_verified,
            "details": self.details,
        }


@dataclass(frozen=True)
class HourCloseCanaryReport:
    cohort: str
    expected_feeds: int
    raw_terminal: int
    coverage_terminal: int
    compressed_terminal: int
    receipts_terminal: int
    unknown_missing: int
    archive_lag_seconds: float
    status: str  # "PASS" | "FAIL" | "DEGRADED"
    observations: tuple[str, ...] = ()
    evaluated_at_utc: str = ""
    eligible_at_utc: str = ""
    is_eligible: bool = True
    feed_statuses: tuple[FeedCanaryStatus, ...] = ()
    s3_uploaded: bool = False
    s3_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cohort": self.cohort,
            "expected_feeds": self.expected_feeds,
            "raw_terminal": self.raw_terminal,
            "coverage_terminal": self.coverage_terminal,
            "compressed_terminal": self.compressed_terminal,
            "receipts_terminal": self.receipts_terminal,
            "unknown_missing": self.unknown_missing,
            "archive_lag_seconds": self.archive_lag_seconds,
            "status": self.status,
            "observations": list(self.observations),
            "evaluated_at_utc": self.evaluated_at_utc,
            "eligible_at_utc": self.eligible_at_utc,
            "is_eligible": self.is_eligible,
            "s3_uploaded": self.s3_uploaded,
            "s3_key": self.s3_key,
            "feeds": [f.to_dict() for f in self.feed_statuses],
        }


def _fsync_file(path: Path) -> None:
    try:
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def _atomic_write_json(target_path: Path, payload: Mapping[str, Any]) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.parent / f".{target_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    data_bytes = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp_path), str(target_path))
        _fsync_file(target_path)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def find_coverage_path(coverage_root: Path, cohort: str, feed: FeedIdentity) -> Path | None:
    """Find coverage evidence file for a feed in cohort."""
    clean_market = feed.market.replace("/", "-").replace(":", "-").lower()
    candidates = [
        coverage_root / cohort / feed.exchange / feed.stream / f"{feed.market}.coverage.json",
        coverage_root / cohort / feed.exchange.lower() / feed.stream.lower() / f"{feed.market}.coverage.json",
        coverage_root / cohort / feed.exchange.lower() / feed.stream.lower() / f"{clean_market}.coverage.json",
        coverage_root / "coverage" / cohort / feed.exchange.lower() / feed.stream.lower() / f"{feed.market}.coverage.json",
        coverage_root / "coverage" / cohort / feed.exchange.lower() / feed.stream.lower() / f"{clean_market}.coverage.json",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def find_raw_path(raw_root: Path, cohort: str, feed: FeedIdentity) -> Path | None:
    """Find raw data file for a feed in cohort."""
    dt_str, hour_str = cohort.split("_")
    clean_market = feed.market.replace("/", "-").replace(":", "-").lower()
    candidates = [
        raw_root / dt_str / feed.exchange.lower() / feed.stream.lower() / f"{feed.exchange.lower()}_{feed.stream.lower()}_{clean_market}_{dt_str}_{hour_str}.jsonl",
        raw_root / dt_str / feed.exchange / feed.stream / f"{feed.exchange}_{feed.stream}_{clean_market}_{dt_str}_{hour_str}.jsonl",
        raw_root / dt_str / feed.exchange.lower() / feed.stream.lower() / f"{feed.exchange.lower()}_{feed.stream.lower()}_{feed.market.lower()}_{dt_str}_{hour_str}.jsonl",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def find_raw_compressed_path(compressed_root: Path, cohort: str, feed: FeedIdentity) -> Path | None:
    """Find raw compressed .jsonl.zst file for a feed in cohort."""
    dt_str, hour_str = cohort.split("_")
    clean_market = feed.market.replace("/", "-").replace(":", "-").lower()
    candidates = [
        compressed_root / dt_str / feed.exchange.lower() / feed.stream.lower() / f"{feed.exchange.lower()}_{feed.stream.lower()}_{clean_market}_{dt_str}_{hour_str}.jsonl.zst",
        compressed_root / dt_str / feed.exchange / feed.stream / f"{feed.exchange}_{feed.stream}_{clean_market}_{dt_str}_{hour_str}.jsonl.zst",
        compressed_root / dt_str / feed.exchange.lower() / feed.stream.lower() / f"{feed.exchange.lower()}_{feed.stream.lower()}_{clean_market}_{dt_str}_{hour_str}.zst",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def find_raw_receipt_path(receipt_root: Path, cohort: str, feed: FeedIdentity) -> Path | None:
    """Find raw archive receipt file for a feed in cohort."""
    dt_str, hour_str = cohort.split("_")
    clean_market = feed.market.replace("/", "-").replace(":", "-").lower()
    candidates = [
        receipt_root / dt_str / feed.exchange.lower() / feed.stream.lower() / f"{feed.exchange.lower()}_{feed.stream.lower()}_{clean_market}_{dt_str}_{hour_str}.jsonl.archive-receipt.json",
        receipt_root / dt_str / feed.exchange / feed.stream / f"{feed.exchange}_{feed.stream}_{clean_market}_{dt_str}_{hour_str}.jsonl.archive-receipt.json",
        receipt_root / dt_str / feed.exchange.lower() / feed.stream.lower() / f"{feed.exchange.lower()}_{feed.stream.lower()}_{clean_market}_{dt_str}_{hour_str}.archive-receipt.json",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def find_coverage_receipt_path(receipt_root: Path, cohort: str, feed: FeedIdentity) -> Path | None:
    """Find coverage archive receipt file for a feed in cohort."""
    clean_market = feed.market.replace("/", "-").replace(":", "-").lower()
    candidates = [
        receipt_root / "coverage" / cohort / feed.exchange.lower() / feed.stream.lower() / f"{feed.market}.coverage.json.archive-receipt.json",
        receipt_root / "coverage" / cohort / feed.exchange / feed.stream / f"{feed.market}.coverage.json.archive-receipt.json",
        receipt_root / "coverage" / cohort / feed.exchange.lower() / feed.stream.lower() / f"{clean_market}.coverage.json.archive-receipt.json",
        receipt_root / cohort / feed.exchange.lower() / feed.stream.lower() / f"{feed.market}.coverage.json.archive-receipt.json",
        receipt_root / cohort / feed.exchange / feed.stream / f"{feed.market}.coverage.json.archive-receipt.json",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def find_coverage_compressed_path(compressed_root: Path, cohort: str, feed: FeedIdentity) -> Path | None:
    """Find coverage compressed .zst file for a feed in cohort."""
    clean_market = feed.market.replace("/", "-").replace(":", "-").lower()
    candidates = [
        compressed_root / "coverage" / cohort / feed.exchange.lower() / feed.stream.lower() / f"{feed.market}.coverage.json.zst",
        compressed_root / "coverage" / cohort / feed.exchange / feed.stream / f"{feed.market}.coverage.json.zst",
        compressed_root / "coverage" / cohort / feed.exchange.lower() / feed.stream.lower() / f"{clean_market}.coverage.json.zst",
        compressed_root / cohort / feed.exchange.lower() / feed.stream.lower() / f"{feed.market}.coverage.json.zst",
        compressed_root / cohort / feed.exchange / feed.stream / f"{feed.market}.coverage.json.zst",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


class HourCloseCanary:
    """Independent completeness auditor for closed UTC hour cohorts."""

    def __init__(
        self,
        base_dir: Path | str | None = None,
        raw_root: Path | str | None = None,
        coverage_root: Path | str | None = None,
        compressed_root: Path | str | None = None,
        receipt_root: Path | str | None = None,
        artifact_dir: Path | str | None = None,
        grace_seconds: float = 600.0,
        s3_bucket: str | None = None,
        s3_prefix: str = "",
        s3_client: Any = None,
        feeds: Sequence[FeedIdentity] = SEALED_FEED_UNIVERSE,
    ) -> None:
        base_path = Path(base_dir).resolve() if base_dir is not None else None

        if raw_root is not None:
            self.raw_root = Path(raw_root).resolve()
        elif base_path is not None:
            self.raw_root = base_path / "raw" if (base_path / "raw").exists() else base_path
        else:
            self.raw_root = Path("raw").resolve()

        if coverage_root is not None:
            self.coverage_root = Path(coverage_root).resolve()
        elif base_path is not None:
            self.coverage_root = base_path / "coverage" if (base_path / "coverage").exists() else base_path
        else:
            self.coverage_root = Path("coverage").resolve()

        if compressed_root is not None:
            self.compressed_root = Path(compressed_root).resolve()
        elif base_path is not None:
            self.compressed_root = base_path / "compressed" if (base_path / "compressed").exists() else base_path
        else:
            self.compressed_root = Path("compressed").resolve()

        if receipt_root is not None:
            self.receipt_root = Path(receipt_root).resolve()
        elif base_path is not None:
            if (base_path / "archive-receipts").exists():
                self.receipt_root = base_path / "archive-receipts"
            elif (base_path / "receipts").exists():
                self.receipt_root = base_path / "receipts"
            else:
                self.receipt_root = base_path
        else:
            self.receipt_root = Path("archive-receipts").resolve()

        if artifact_dir is not None:
            self.artifact_dir = Path(artifact_dir).resolve()
        elif base_path is not None:
            self.artifact_dir = base_path / "canary"
        else:
            self.artifact_dir = Path("canary").resolve()

        self.grace_seconds = float(grace_seconds)
        self.s3_bucket = s3_bucket
        self.s3_prefix = s3_prefix.strip("/")
        self.s3_client = s3_client
        self.feeds = tuple(feeds)

    def _evaluate_feed(
        self,
        cohort: str,
        feed: FeedIdentity,
    ) -> tuple[FeedCanaryStatus, datetime | None]:
        details: dict[str, Any] = {}
        ts_candidates: list[datetime] = []
        cov_binding = None  # [BINDING] Initialize for downstream use

        # 1. Coverage Evidence Inspection
        cov_path = find_coverage_path(self.coverage_root, cohort, feed)
        coverage_terminal = False
        is_verified_zero = False

        if cov_path is not None:
            details["coverage_path"] = str(cov_path)
            try:
                cov = load_feed_hour_coverage(cov_path)
                details["coverage_state"] = cov.coverage_state
                details["event_count"] = cov.event_count

                # [BINDING] Extract coverage binding metadata
                cov_binding = cov.data_artifact_binding
                details["data_artifact_binding_present"] = cov_binding is not None
                if cov.coverage_state == "DATA_PRESENT" and cov_binding is None:
                    details["binding_warning"] = "DATA_PRESENT_WITHOUT_BINDING"

                if cov.coverage_state in ("DATA_PRESENT", "VERIFIED_ZERO_EVENT"):
                    coverage_terminal = True
                    is_verified_zero = (cov.coverage_state == "VERIFIED_ZERO_EVENT")
                if cov.closed_at_utc:
                    try:
                        ts = datetime.fromisoformat(cov.closed_at_utc.replace("Z", "+00:00"))
                        ts_candidates.append(ts)
                    except ValueError:
                        pass
            except Exception as exc:
                details["coverage_error"] = str(exc)
        else:
            details["coverage_path"] = None

        # 2. Raw Data Inspection (or VERIFIED_ZERO)
        raw_terminal = False
        if is_verified_zero:
            # Contract: verified zero events produce no raw file, which is validly terminal
            raw_terminal = True
            details["raw_state"] = "VERIFIED_ZERO_CONTRACT"
            # [BINDING] VERIFIED_ZERO should not claim raw data exists
            if cov_binding is not None and cov_binding.raw_size > 0:
                details["binding_warning"] = "VERIFIED_ZERO_WITH_RAW_BINDING"
        else:
            raw_path = find_raw_path(self.raw_root, cohort, feed)
            if raw_path is not None and raw_path.is_file() and raw_path.stat().st_size > 0:
                raw_terminal = True
                details["raw_path"] = str(raw_path)
                details["raw_state"] = "PRESENT"
                # [BINDING] Verify raw file checksum against binding
                if cov_binding is not None:
                    actual_hash = file_sha256(raw_path)
                    if actual_hash != cov_binding.raw_sha256:
                        details["raw_state"] = "CHECKSUM_MISMATCH"
                        details["raw_sha256_actual"] = actual_hash
                        details["raw_sha256_expected"] = cov_binding.raw_sha256
                        raw_terminal = False
                    else:
                        details["raw_sha256_verified"] = True
            else:
                # [BINDING] Raw missing but binding claims it exists
                if cov_binding is not None and cov_binding.raw_size > 0:
                    details["binding_warning"] = "RAW_MISSING_BINDING_EXISTS"
                # Check if raw partition was already archived and cleaned up via valid receipt
                raw_rec_path = find_raw_receipt_path(self.receipt_root, cohort, feed)
                if raw_rec_path is not None and raw_rec_path.is_file():
                    try:
                        rec_data = json.loads(raw_rec_path.read_text(encoding="utf-8"))
                        if rec_data.get("state") == "CLEANED" and (rec_data.get("source_size", 0) or 0) > 0:
                            raw_terminal = True
                            details["raw_state"] = "CLEANED_CONFIRMED"
                        else:
                            details["raw_state"] = "MISSING_WITH_RECEIPT"
                    except Exception:
                        details["raw_state"] = "MISSING"
                else:
                    details["raw_state"] = "MISSING"

        # 3. Compressed Data Inspection (.jsonl.zst)
        compressed_terminal = False
        if is_verified_zero:
            # Contract: zero events produce no raw data partition to compress to .jsonl.zst
            cov_zst = find_coverage_compressed_path(self.compressed_root, cohort, feed)
            if cov_zst is not None and cov_zst.is_file() and cov_zst.stat().st_size > 0:
                compressed_terminal = True
                details["compressed_state"] = "COVERAGE_ZST_PRESENT"
            else:
                # Zero raw data produces no .jsonl.zst, terminal by zero contract
                compressed_terminal = True
                details["compressed_state"] = "ZERO_EVENT_EXEMPT"
        else:
            comp_path = find_raw_compressed_path(self.compressed_root, cohort, feed)
            if comp_path is not None and comp_path.is_file() and comp_path.stat().st_size > 0:
                compressed_terminal = True
                details["compressed_path"] = str(comp_path)
                details["compressed_state"] = "PRESENT"
            else:
                details["compressed_state"] = "MISSING"

        # 4. Receipt Inspection
        receipts_terminal = False
        if is_verified_zero:
            # For verified zero, coverage receipt verifies archival
            cov_rec_path = find_coverage_receipt_path(self.receipt_root, cohort, feed)
            if cov_rec_path is not None and cov_rec_path.is_file():
                try:
                    cov_rec_data = json.loads(cov_rec_path.read_text(encoding="utf-8"))
                    if (
                        cov_rec_data.get("state") in QUALIFYING_RECEIPT_STATES
                        and cov_rec_data.get("restore_verified_at") is not None
                    ):
                        receipts_terminal = True
                        details["receipt_state"] = cov_rec_data.get("state")
                        restore_ts = cov_rec_data.get("restore_verified_at")
                        if restore_ts:
                            try:
                                ts_candidates.append(datetime.fromisoformat(restore_ts.replace("Z", "+00:00")))
                            except ValueError:
                                pass
                    else:
                        details["receipt_state"] = f"NON_QUALIFYING: {cov_rec_data.get('state')}"
                except Exception as exc:
                    details["receipt_error"] = str(exc)
            else:
                # If no separate coverage receipt exists, check if raw receipt was somehow written
                raw_rec_path = find_raw_receipt_path(self.receipt_root, cohort, feed)
                if raw_rec_path is not None and raw_rec_path.is_file():
                    try:
                        r_data = json.loads(raw_rec_path.read_text(encoding="utf-8"))
                        receipts_terminal = bool(
                            r_data.get("state") in QUALIFYING_RECEIPT_STATES
                            and r_data.get("restore_verified_at") is not None
                        )
                    except Exception:
                        pass
                if not receipts_terminal:
                    details["receipt_state"] = "MISSING"
        else:
            raw_rec_path = find_raw_receipt_path(self.receipt_root, cohort, feed)
            if raw_rec_path is not None and raw_rec_path.is_file():
                try:
                    raw_rec_data = json.loads(raw_rec_path.read_text(encoding="utf-8"))
                    details["receipt_state"] = raw_rec_data.get("state")
                    if (
                        raw_rec_data.get("state") in QUALIFYING_RECEIPT_STATES
                        and raw_rec_data.get("restore_verified_at") is not None
                    ):
                        receipts_terminal = True
                        restore_ts = raw_rec_data.get("restore_verified_at")
                        if restore_ts:
                            try:
                                ts_candidates.append(datetime.fromisoformat(restore_ts.replace("Z", "+00:00")))
                            except ValueError:
                                pass
                    else:
                        details["receipt_state"] = f"NON_QUALIFYING: {raw_rec_data.get('state')}"
                    # [BINDING] Cross-reference receipt source_sha256 against coverage binding
                    if cov_binding is not None:
                        if raw_rec_data.get("source_sha256") != cov_binding.raw_sha256:
                            details["receipt_binding_mismatch"] = True
                            receipts_terminal = False
                except Exception as exc:
                    details["receipt_error"] = str(exc)
            else:
                details["receipt_state"] = "MISSING"

        # 5. Unknown missing
        unknown_missing = not (coverage_terminal or raw_terminal or compressed_terminal or receipts_terminal)

        # [BINDING] Determine overall binding verification status
        binding_ok = (
            cov_binding is not None
            and details.get("coverage_state") == "DATA_PRESENT"
            and details.get("raw_sha256_verified") is True
        )

        feed_status = FeedCanaryStatus(
            feed=feed,
            raw_terminal=raw_terminal,
            coverage_terminal=coverage_terminal,
            compressed_terminal=compressed_terminal,
            receipts_terminal=receipts_terminal,
            is_verified_zero=is_verified_zero,
            unknown_missing=unknown_missing,
            binding_verified=binding_ok,
            details=details,
        )
        feed_ts = max(ts_candidates) if ts_candidates else None
        return feed_status, feed_ts

    def inspect_cohort(
        self,
        cohort: str,
        now: datetime | None = None,
        allow_early: bool = False,
        emit_local: bool = True,
        upload_s3: bool = True,
    ) -> HourCloseCanaryReport:
        """Inspect hour cohort completeness across all 76 feeds."""
        cohort_id = ArchiveCohortId.parse(cohort)
        dt_str, hour_str = cohort_id.date_str, cohort_id.hour_str
        year, month, day = map(int, dt_str.split("-"))
        cohort_start = datetime(year, month, day, int(hour_str), tzinfo=timezone.utc)
        cohort_close = cohort_start + timedelta(hours=1)
        eligible_at = cohort_close + timedelta(seconds=self.grace_seconds)

        cur_now = now or datetime.now(timezone.utc)
        if cur_now.tzinfo is None:
            cur_now = cur_now.replace(tzinfo=timezone.utc)

        is_eligible = (cur_now >= eligible_at)
        if not is_eligible and not allow_early:
            raise CohortNotEligibleError(
                f"Cohort {cohort} closes at {cohort_close.strftime('%Y-%m-%dT%H:%M:%SZ')}, "
                f"eligible after grace at {eligible_at.strftime('%Y-%m-%dT%H:%M:%SZ')}. "
                f"Current evaluation time: {cur_now.strftime('%Y-%m-%dT%H:%M:%SZ')}."
            )

        # Evaluate exactly all 76 feeds (FAIL-CLOSED, READ-ONLY)
        raw_terminal_count = 0
        coverage_terminal_count = 0
        compressed_terminal_count = 0
        receipts_terminal_count = 0
        unknown_missing_count = 0
        verified_zero_count = 0
        feed_statuses: list[FeedCanaryStatus] = []
        completion_timestamps: list[datetime] = []

        for feed in self.feeds:
            status, ts = self._evaluate_feed(cohort, feed)
            feed_statuses.append(status)
            if status.is_verified_zero:
                verified_zero_count += 1
            if status.raw_terminal:
                raw_terminal_count += 1
            if status.coverage_terminal:
                coverage_terminal_count += 1
            if status.compressed_terminal:
                compressed_terminal_count += 1
            if status.receipts_terminal:
                receipts_terminal_count += 1
            if status.unknown_missing:
                unknown_missing_count += 1
            if ts is not None:
                completion_timestamps.append(ts)

        # Compute archive lag
        total_expected = len(self.feeds)
        all_terminal = (
            raw_terminal_count == total_expected
            and coverage_terminal_count == total_expected
            and compressed_terminal_count == total_expected
            and receipts_terminal_count == total_expected
        )
        if all_terminal and completion_timestamps:
            latest_completion = max(completion_timestamps)
            archive_lag_seconds = max(0.0, (latest_completion - cohort_close).total_seconds())
        else:
            archive_lag_seconds = max(0.0, (cur_now - cohort_close).total_seconds())

        observations: list[str] = []

        # CANARY MUST NOT ALTER RUN:
        # Detect V4-style failure: coverage present, but RAW missing (0/76)
        non_zero_expected = total_expected - verified_zero_count
        raw_data_present_count = raw_terminal_count - verified_zero_count

        if coverage_terminal_count > 0 and raw_data_present_count == 0 and non_zero_expected > 0:
            observations.append(
                f"HIGH_SEVERITY_OBSERVATION: RAW data missing (0/{non_zero_expected}) "
                f"while coverage evidence is present ({coverage_terminal_count}/{total_expected}). "
                f"V4-style failure pattern detected. Fail-closed: canary records observation and DOES NOT "
                f"repair, restart, or fabricate data."
            )
            verdict = "FAIL"
        elif unknown_missing_count == total_expected:
            observations.append(
                f"HIGH_SEVERITY_OBSERVATION: All {total_expected} expected feeds are completely missing from archive."
            )
            verdict = "FAIL"
        elif all_terminal and unknown_missing_count == 0:
            verdict = "PASS"
            observations.append(
                f"Cohort {cohort} completeness fully verified: {total_expected} feeds terminal "
                f"(raw={raw_terminal_count}, coverage={coverage_terminal_count}, "
                f"compressed={compressed_terminal_count}, receipts={receipts_terminal_count}, "
                f"verified_zero={verified_zero_count}, lag={archive_lag_seconds:.1f}s)."
            )
        else:
            # Completeness is degraded or failing
            if raw_terminal_count < total_expected * 0.5 or coverage_terminal_count < total_expected * 0.5:
                verdict = "FAIL"
                observations.append(
                    f"HIGH_SEVERITY_OBSERVATION: Severe feed deficit: raw={raw_terminal_count}/{total_expected}, "
                    f"coverage={coverage_terminal_count}/{total_expected}."
                )
            else:
                verdict = "DEGRADED"
                observations.append(
                    f"Completeness degraded: raw={raw_terminal_count}/{total_expected}, "
                    f"coverage={coverage_terminal_count}/{total_expected}, "
                    f"compressed={compressed_terminal_count}/{total_expected}, "
                    f"receipts={receipts_terminal_count}/{total_expected}, "
                    f"unknown_missing={unknown_missing_count}/{total_expected}."
                )

        s3_uploaded = False
        s3_key: str | None = None

        report = HourCloseCanaryReport(
            cohort=cohort,
            expected_feeds=total_expected,
            raw_terminal=raw_terminal_count,
            coverage_terminal=coverage_terminal_count,
            compressed_terminal=compressed_terminal_count,
            receipts_terminal=receipts_terminal_count,
            unknown_missing=unknown_missing_count,
            archive_lag_seconds=round(archive_lag_seconds, 3),
            status=verdict,
            observations=tuple(observations),
            evaluated_at_utc=cur_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            eligible_at_utc=eligible_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            is_eligible=is_eligible,
            feed_statuses=tuple(feed_statuses),
            s3_uploaded=False,
            s3_key=None,
        )

        # Emit local artifact (NON-MUTATING towards raw data, only writes report to artifact_dir)
        artifact_file = self.artifact_dir / f"hour-close-{cohort}.json"
        if emit_local:
            _atomic_write_json(artifact_file, report.to_dict())

        # Optionally upload to S3 <s3_prefix>/canary/hour-close-<cohort>.json
        if upload_s3 and self.s3_bucket:
            s3_key = f"{self.s3_prefix}/canary/hour-close-{cohort}.json" if self.s3_prefix else f"canary/hour-close-{cohort}.json"
            data_bytes = (json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            s3_client = self.s3_client
            if s3_client is None:
                try:
                    import boto3  # pyright: ignore[reportMissingImports]
                    s3_client = boto3.client("s3")
                except Exception as exc:
                    raise HourCloseCanaryError(f"Failed to initialize S3 client: {exc}") from exc
            try:
                s3_client.put_object(
                    Bucket=self.s3_bucket,
                    Key=s3_key,
                    Body=data_bytes,
                    ContentType="application/json",
                )
                s3_uploaded = True
            except Exception as exc:
                raise HourCloseCanaryError(f"Failed to upload canary artifact to S3 s3://{self.s3_bucket}/{s3_key}: {exc}") from exc

            report = HourCloseCanaryReport(
                cohort=report.cohort,
                expected_feeds=report.expected_feeds,
                raw_terminal=report.raw_terminal,
                coverage_terminal=report.coverage_terminal,
                compressed_terminal=report.compressed_terminal,
                receipts_terminal=report.receipts_terminal,
                unknown_missing=report.unknown_missing,
                archive_lag_seconds=report.archive_lag_seconds,
                status=report.status,
                observations=report.observations,
                evaluated_at_utc=report.evaluated_at_utc,
                eligible_at_utc=report.eligible_at_utc,
                is_eligible=report.is_eligible,
                feed_statuses=report.feed_statuses,
                s3_uploaded=s3_uploaded,
                s3_key=s3_key,
            )
            if emit_local:
                # Update local artifact with s3 metadata
                _atomic_write_json(artifact_file, report.to_dict())

        return report
