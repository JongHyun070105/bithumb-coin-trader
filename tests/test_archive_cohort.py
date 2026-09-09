"""Canonical UTC date-hour identity for archive cohorts."""

from __future__ import annotations

import unittest

from bithumb_coin_trader.archive_cohort import ArchiveCohortId, classify_archive_identity


class ArchiveCohortIdTests(unittest.TestCase):
    def test_canonical_render_parse_and_partition_extraction(self) -> None:
        cohort = ArchiveCohortId("2026-09-05", "05")

        self.assertEqual(cohort.key, "2026-09-05_05")
        self.assertEqual(str(cohort), "2026-09-05_05")
        self.assertEqual(ArchiveCohortId.parse("2026-09-05_05"), cohort)
        self.assertEqual(
            ArchiveCohortId.from_partition_name("BTC_KRW_2026-09-05_05.jsonl"),
            cohort,
        )

    def test_rejects_invalid_dates_and_hours(self) -> None:
        invalid = (
            ("2026-02-30", "05"),
            ("2026-9-05", "05"),
            ("2026-09-05", "5"),
            ("2026-09-05", "24"),
            ("2026-09-05", "-1"),
        )
        for date_str, hour_str in invalid:
            with self.subTest(date=date_str, hour=hour_str):
                with self.assertRaises(ValueError):
                    ArchiveCohortId(date_str, hour_str)

        with self.assertRaises(ValueError):
            ArchiveCohortId.parse("05")
        with self.assertRaises(ValueError):
            ArchiveCohortId.from_partition_name("BTC_KRW_05.jsonl")

    def test_sorts_chronologically_and_distinguishes_same_hour(self) -> None:
        cohorts = [
            ArchiveCohortId("2026-09-07", "05"),
            ArchiveCohortId("2026-09-05", "06"),
            ArchiveCohortId("2026-09-05", "05"),
            ArchiveCohortId("2026-09-06", "05"),
        ]

        self.assertEqual(
            [cohort.key for cohort in sorted(cohorts)],
            ["2026-09-05_05", "2026-09-05_06", "2026-09-06_05", "2026-09-07_05"],
        )
        self.assertEqual(len(set(cohorts)), 4)

    def test_hour_only_identity_is_legacy_and_non_qualifying(self) -> None:
        diagnostic = classify_archive_identity("05")

        self.assertEqual(diagnostic.status, "LEGACY / NON-QUALIFYING")
        self.assertIsNone(diagnostic.cohort)
        self.assertFalse(diagnostic.qualifies)

        canonical = classify_archive_identity("2026-09-05_05")
        self.assertEqual(canonical.status, "CANONICAL")
        self.assertEqual(canonical.cohort, ArchiveCohortId("2026-09-05", "05"))
        self.assertTrue(canonical.qualifies)


if __name__ == "__main__":
    unittest.main()
