"""Unit tests for latency measurement protocol and statistics."""

import os
import pytest

from scripts.measure_execution_latency import (
    compute_latency_profile,
    generate_synthetic_latency_sample,
    run_latency_measurement,
)


def test_compute_latency_profile_known_values():
    delays = [10.0, 20.0, 30.0, 40.0, 50.0]
    profile = compute_latency_profile(delays, mode="TEST")

    assert profile.sample_count == 5
    assert profile.min_ms == 10.0
    assert profile.max_ms == 50.0
    assert profile.mean_ms == 30.0
    assert profile.p50_ms == 30.0
    assert profile.mode == "TEST"


def test_percentile_monotonicity():
    samples = generate_synthetic_latency_sample(count=500, seed=123)
    profile = compute_latency_profile(samples)

    assert profile.min_ms <= profile.p50_ms
    assert profile.p50_ms <= profile.p90_ms
    assert profile.p90_ms <= profile.p95_ms
    assert profile.p95_ms <= profile.p99_ms
    assert profile.p99_ms <= profile.p99_9_ms
    assert profile.p99_9_ms <= profile.max_ms


def test_live_probe_blocked_fail_closed(monkeypatch):
    monkeypatch.delenv("ALLOW_LIVE_PROBING", raising=False)
    exit_code = run_latency_measurement(live=True)
    assert exit_code != 0
