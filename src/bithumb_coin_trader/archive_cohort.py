"""Canonical UTC date-hour identity used by the archive pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Optional


_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HOUR_PATTERN = re.compile(r"^(?:[01]\d|2[0-3])$")
_KEY_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})_([0-2]\d)$")
_PARTITION_PATTERN = re.compile(r"_(\d{4}-\d{2}-\d{2})_([0-2]\d)\.jsonl$")


@dataclass(frozen=True, order=True)
class ArchiveCohortId:
    """An immutable closed UTC-hour identity."""

    date_str: str
    hour_str: str

    def __post_init__(self) -> None:
        if not _DATE_PATTERN.fullmatch(self.date_str):
            raise ValueError("archive cohort date must use YYYY-MM-DD")
        try:
            date.fromisoformat(self.date_str)
        except ValueError as exc:
            raise ValueError("archive cohort date is not a real calendar date") from exc
        if not _HOUR_PATTERN.fullmatch(self.hour_str):
            raise ValueError("archive cohort hour must be two digits from 00 through 23")

    @property
    def key(self) -> str:
        return f"{self.date_str}_{self.hour_str}"

    def __str__(self) -> str:
        return self.key

    @classmethod
    def parse(cls, value: str) -> "ArchiveCohortId":
        match = _KEY_PATTERN.fullmatch(value)
        if not match:
            raise ValueError("archive cohort key must use YYYY-MM-DD_HH")
        return cls(match.group(1), match.group(2))

    @classmethod
    def from_partition_name(cls, name: str) -> "ArchiveCohortId":
        match = _PARTITION_PATTERN.search(name)
        if not match:
            raise ValueError("partition name does not contain a canonical date-hour cohort")
        return cls(match.group(1), match.group(2))


@dataclass(frozen=True)
class ArchiveIdentityDiagnostic:
    status: str
    cohort: Optional[ArchiveCohortId]

    @property
    def qualifies(self) -> bool:
        return self.cohort is not None and self.status == "CANONICAL"


def classify_archive_identity(value: str) -> ArchiveIdentityDiagnostic:
    """Classify persisted identity without promoting hour-only legacy evidence."""

    if re.fullmatch(r"\d{2}", value):
        return ArchiveIdentityDiagnostic("LEGACY / NON-QUALIFYING", None)
    try:
        cohort = ArchiveCohortId.parse(value)
    except ValueError:
        return ArchiveIdentityDiagnostic("INVALID / NON-QUALIFYING", None)
    return ArchiveIdentityDiagnostic("CANONICAL", cohort)
