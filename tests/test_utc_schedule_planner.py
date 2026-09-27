"""Unit tests for deterministic Full UTC Hour Schedule Planner."""

from datetime import datetime, timezone
import unittest

from bithumb_coin_trader.utc_schedule_planner import (
    compute_full_utc_hour_schedule,
    UtcSchedulePlan,
)


class UtcSchedulePlannerTests(unittest.TestCase):
    def test_v3b_actual_start_schedule_calculation(self) -> None:
        """Reproduce V3B start time (2026-09-17 06:25:56.906990Z) and verify exact timing contract."""
        start_v3b = datetime(2026, 9, 17, 6, 25, 56, 906990, tzinfo=timezone.utc)
        plan = compute_full_utc_hour_schedule(
            start_time=start_v3b,
            target_full_hours=1,
            grace_seconds=600,
            post_grace_settle_seconds=180,
            keep_collecting_through_grace=True,
        )

        # 1. Warmup: from 06:25:56.906990 to 07:00:00.000000
        # 34m 3.093010s = 2043.093010 seconds
        expected_warmup = (datetime(2026, 9, 17, 7, 0, 0, tzinfo=timezone.utc) - start_v3b).total_seconds()
        self.assertAlmostEqual(plan.warmup_duration_seconds, expected_warmup, places=5)
        self.assertEqual(plan.partial_start_cohort, "2026-09-17_06")

        # 2. Qualifying cohort: exact 1 hour from 07:00 to 08:00
        self.assertEqual(plan.qualification_start_utc, "2026-09-17T07:00:00+00:00")
        self.assertEqual(plan.qualifying_cohorts, ("2026-09-17_07",))
        self.assertEqual(plan.cohort_closure_utc, "2026-09-17T08:00:00+00:00")

        # 3. Grace and settle
        self.assertEqual(plan.grace_expiry_utc, "2026-09-17T08:10:00+00:00")
        self.assertEqual(plan.archive_settled_utc, "2026-09-17T08:13:00+00:00")

        # 4. Total durations
        # Warmup (2043.093010) + 1 hour (3600) + Grace (600) + Settle (180) = 6423.093010s (~107 minutes)
        expected_total = expected_warmup + 3600 + 600 + 180
        self.assertAlmostEqual(plan.total_pipeline_duration_seconds, expected_total, places=5)
        self.assertAlmostEqual(plan.collection_duration_seconds, expected_total, places=5)
        self.assertEqual(plan.partial_end_cohort, "2026-09-17_08")

    def test_exact_utc_boundary_start(self) -> None:
        """When starting exactly on the UTC hour boundary (e.g. 07:00:00Z), warmup is 0s."""
        start_exact = datetime(2026, 9, 17, 7, 0, 0, tzinfo=timezone.utc)
        plan = compute_full_utc_hour_schedule(
            start_time=start_exact,
            target_full_hours=1,
            grace_seconds=600,
            post_grace_settle_seconds=180,
        )

        self.assertEqual(plan.warmup_duration_seconds, 0.0)
        self.assertIsNone(plan.partial_start_cohort)
        self.assertEqual(plan.qualification_start_utc, "2026-09-17T07:00:00+00:00")
        self.assertEqual(plan.qualifying_cohorts, ("2026-09-17_07",))
        self.assertEqual(plan.cohort_closure_utc, "2026-09-17T08:00:00+00:00")
        self.assertEqual(plan.grace_expiry_utc, "2026-09-17T08:10:00+00:00")
        self.assertEqual(plan.archive_settled_utc, "2026-09-17T08:13:00+00:00")

    def test_multi_hour_target(self) -> None:
        """Targeting 3 full hours produces 3 qualifying cohorts in sequence."""
        start = datetime(2026, 9, 17, 6, 30, 0, tzinfo=timezone.utc)
        plan = compute_full_utc_hour_schedule(
            start_time=start,
            target_full_hours=3,
            grace_seconds=600,
            post_grace_settle_seconds=300,
        )

        self.assertEqual(plan.warmup_duration_seconds, 1800.0)
        self.assertEqual(plan.qualifying_cohorts, ("2026-09-17_07", "2026-09-17_08", "2026-09-17_09"))
        self.assertEqual(plan.cohort_closure_utc, "2026-09-17T10:00:00+00:00")
        self.assertEqual(plan.grace_expiry_utc, "2026-09-17T10:10:00+00:00")
        self.assertEqual(plan.archive_settled_utc, "2026-09-17T10:15:00+00:00")

    def test_fail_closed_validations(self) -> None:
        """Invalid inputs fail closed."""
        start = datetime(2026, 9, 17, 6, 0, 0, tzinfo=timezone.utc)

        # Naive datetime
        with self.assertRaises(ValueError):
            compute_full_utc_hour_schedule(datetime(2026, 9, 17, 6, 0, 0))

        # Grace seconds < 600
        with self.assertRaises(ValueError):
            compute_full_utc_hour_schedule(start, grace_seconds=599)

        # Target hours < 1
        with self.assertRaises(ValueError):
            compute_full_utc_hour_schedule(start, target_full_hours=0)

        # Negative post grace settle
        with self.assertRaises(ValueError):
            compute_full_utc_hour_schedule(start, post_grace_settle_seconds=-1)


if __name__ == "__main__":
    unittest.main()
