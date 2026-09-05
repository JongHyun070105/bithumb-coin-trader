#!/usr/bin/env python3
"""Execution Latency Measurement & Analysis Tool.

Operates in safe dry-run / synthetic mode by default.
LIVE NETWORK PROBING IS BLOCKED during the 72H soak period.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import random
import sys
from typing import Sequence


@dataclass(frozen=True, slots=True)
class LatencyProfile:
    sample_count: int
    min_ms: float
    p50_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    p99_9_ms: float
    max_ms: float
    mean_ms: float
    std_ms: float
    mode: str  # "SYNTHETIC_DRY_RUN" or "MEASURED_LIVE"

    def to_dict(self) -> dict[str, float | int | str]:
        return asdict(self)


def compute_latency_profile(
    delays_ms: Sequence[float],
    mode: str = "SYNTHETIC_DRY_RUN",
) -> LatencyProfile:
    if not delays_ms:
        raise ValueError("delays_ms cannot be empty")

    sorted_delays = sorted(delays_ms)
    n = len(sorted_delays)

    def percentile(p: float) -> float:
        k = (n - 1) * (p / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_delays[int(k)]
        d0 = sorted_delays[int(f)] * (c - k)
        d1 = sorted_delays[int(c)] * (k - f)
        return d0 + d1

    mean_val = sum(sorted_delays) / n
    variance = sum((x - mean_val) ** 2 for x in sorted_delays) / n
    std_val = math.sqrt(variance)

    return LatencyProfile(
        sample_count=n,
        min_ms=round(sorted_delays[0], 2),
        p50_ms=round(percentile(50.0), 2),
        p90_ms=round(percentile(90.0), 2),
        p95_ms=round(percentile(95.0), 2),
        p99_ms=round(percentile(99.0), 2),
        p99_9_ms=round(percentile(99.9), 2),
        max_ms=round(sorted_delays[-1], 2),
        mean_ms=round(mean_val, 2),
        std_ms=round(std_val, 2),
        mode=mode,
    )


def generate_synthetic_latency_sample(
    count: int = 1000,
    base_mu_ms: float = 35.0,
    sigma: float = 0.4,
    seed: int = 42,
) -> list[float]:
    """Generates a realistic log-normal network latency distribution."""
    rng = random.Random(seed)
    log_mu = math.log(base_mu_ms)
    samples: list[float] = []
    for _ in range(count):
        val = rng.lognormvariate(log_mu, sigma)
        # Add occasional queue spike
        if rng.random() < 0.02:
            val += rng.uniform(50.0, 300.0)
        samples.append(val)
    return samples


def run_latency_measurement(
    samples_count: int = 1000,
    live: bool = False,
    output_path: Path | None = None,
) -> int:
    print("=" * 80)
    print("EXECUTION LATENCY MEASUREMENT TOOL")
    print("=" * 80)

    if live:
        if os.environ.get("ALLOW_LIVE_PROBING") != "1":
            print(
                "BLOCKED: Live network probing is strictly disabled during the 72H soak period.\n"
                "To run synthetic offline profiling, run without --live flag.",
                file=sys.stderr,
            )
            return 1
        print("Running LIVE network probe to Bithumb endpoints...")
        # Live socket probe skeleton (omitted/unreachable during 72H)
        return 1

    print(f"Running in SAFE OFFLINE SYNTHETIC MODE (N={samples_count} samples)...")
    samples = generate_synthetic_latency_sample(count=samples_count)
    profile = compute_latency_profile(samples, mode="SYNTHETIC_DRY_RUN")

    print("\nEMPIRICAL LATENCY PROFILE:")
    print(f"  Mode:         {profile.mode}")
    print(f"  Sample Count: {profile.sample_count}")
    print(f"  Min:          {profile.min_ms} ms")
    print(f"  Median (p50): {profile.p50_ms} ms")
    print(f"  p90:          {profile.p90_ms} ms")
    print(f"  p95:          {profile.p95_ms} ms")
    print(f"  p99:          {profile.p99_ms} ms")
    print(f"  p99.9:        {profile.p99_9_ms} ms")
    print(f"  Max:          {profile.max_ms} ms")
    print(f"  Mean:         {profile.mean_ms} ms (Std: {profile.std_ms} ms)")
    print("=" * 80)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(profile.to_dict(), f, indent=2)
        print(f"Profile saved to: {output_path}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure and model execution latency.")
    parser.add_argument("--samples", type=int, default=1000, help="Number of samples to generate")
    parser.add_argument("--live", action="store_true", help="Attempt live network probing (fails-closed)")
    parser.add_argument("--output", type=Path, default=None, help="Optional output JSON path")
    args = parser.parse_args()
    return run_latency_measurement(args.samples, args.live, args.output)


if __name__ == "__main__":
    sys.exit(main())
