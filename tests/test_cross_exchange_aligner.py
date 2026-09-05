"""Unit and Jitter Tests for Backward As-Of Cross-Exchange Aligner (P1.4, P1.5).

Verifies:
1. Strict backward as-of rule (never selects future events even if closer in time).
2. Maximum staleness threshold enforcement.
3. Missing reference handling.
4. Synthetic jitter / packet delay harness.
"""

from __future__ import annotations

import unittest

from bithumb_coin_trader.cross_exchange_aligner import BackwardAsOfAligner


class BackwardAsOfAlignerTests(unittest.TestCase):
    def test_never_selects_future_reference_even_if_closer(self) -> None:
        """Prove nearest-neighbor fallacy is avoided: only t_ref <= t_target allowed."""
        # Target decision at t = 100.000s
        # Ref A at t = 99.800s (200ms in the past)
        # Ref B at t = 100.050s (50ms in the FUTURE - closer, but illegal!)
        ref_stream = [
            (99.800, "Ref_A_Past"),
            (100.050, "Ref_B_Future"),
        ]

        aligner = BackwardAsOfAligner(ref_stream, max_staleness_ms=1000.0)
        res = aligner.align_as_of(100.000)

        self.assertEqual(res.status, "ALIGNED")
        self.assertEqual(res.reference_item, "Ref_A_Past", "Future reference Ref_B was illegally joined!")
        self.assertEqual(res.reference_timestamp, 99.800)

    def test_staleness_threshold_rejects_old_reference(self) -> None:
        """Reference older than max_staleness_ms must return STALE_REFERENCE."""
        ref_stream = [
            (90.000, "Ref_Ancient"),
        ]

        # Target at t = 100.000s (10s staleness = 10000ms > max_staleness_ms=2000ms)
        aligner = BackwardAsOfAligner(ref_stream, max_staleness_ms=2000.0)
        res = aligner.align_as_of(100.000)

        self.assertEqual(res.status, "STALE_REFERENCE")
        self.assertEqual(res.reference_item, "Ref_Ancient")
        self.assertAlmostEqual(res.staleness_ms, 10000.0, places=3)

    def test_missing_reference_prior_to_first_event(self) -> None:
        """Querying before first reference event returns MISSING_REFERENCE."""
        ref_stream = [
            (105.000, "First_Ref"),
        ]

        aligner = BackwardAsOfAligner(ref_stream)
        res = aligner.align_as_of(100.000)

        self.assertEqual(res.status, "MISSING_REFERENCE")
        self.assertIsNone(res.reference_item)

    def test_synthetic_jitter_and_reordering_harness(self) -> None:
        """Harness with out-of-order stream and variable jitter recovers true causal past."""
        # Stream supplied in randomized arrival order
        raw_stream = [
            (101.500, "Event_3"),
            (100.200, "Event_1"),
            (102.800, "Event_4"),
            (100.900, "Event_2"),
        ]

        aligner = BackwardAsOfAligner(raw_stream, max_staleness_ms=5000.0)

        # Decision at 101.000s -> latest is Event_2 at 100.900s
        res = aligner.align_as_of(101.000)
        self.assertEqual(res.status, "ALIGNED")
        self.assertEqual(res.reference_item, "Event_2")

        # Decision at 102.000s -> latest is Event_3 at 101.500s
        res2 = aligner.align_as_of(102.000)
        self.assertEqual(res2.status, "ALIGNED")
        self.assertEqual(res2.reference_item, "Event_3")


if __name__ == "__main__":
    unittest.main()
