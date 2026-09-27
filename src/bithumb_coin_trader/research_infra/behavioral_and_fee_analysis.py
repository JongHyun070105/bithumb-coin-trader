"""Retired legacy analysis interface with a retained composition identity helper.

The former report generator used hardcoded, untraceable fees, PnL, monthly maker
rates, and symbol weights. Its fee and change-point entrypoints now fail closed.
Use the raw-input audit scripts for reproducible results:

* ``scripts/audit_external_bitmex_claims.py``
* ``scripts/analyze_external_execution_behavior.py``
* ``scripts/analyze_external_fee_sensitivity.py``

``CompositionAnalyzer`` remains available as a generic algebraic identity; its
output is only as valid as the caller-supplied rates and weights.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any, Mapping, NoReturn, Sequence


_RETIRED_REASON = (
    "Legacy behavioral/fee audit disabled: its historical outputs used hardcoded "
    "and unverified inputs. Run the independent raw-input audit scripts instead."
)


class LegacyAnalysisDisabled(RuntimeError):
    """Raised when a retired hardcoded analysis entrypoint is invoked."""


def _fail_closed() -> NoReturn:
    raise LegacyAnalysisDisabled(_RETIRED_REASON)


class FeeEconomicsAnalyzer:
    """Compatibility name for the retired, non-reproducible fee analysis."""

    @classmethod
    def evaluate_scenarios(cls, *_args: Any, **_kwargs: Any) -> NoReturn:
        _fail_closed()


class ChangePointAnalyzer:
    """Compatibility name for the retired hardcoded OLS-CUSUM analysis."""

    @staticmethod
    def detect_cusum_breaks(
        _series: Sequence[float],
        _labels: Sequence[str],
        _critical_value: float = 1.358,
    ) -> NoReturn:
        _fail_closed()


@dataclass(frozen=True)
class CompositionDecomposition:
    period_1_label: str
    period_2_label: str
    period_1_maker_ratio: float
    period_2_maker_ratio: float
    total_change: float
    intra_symbol_effect: float
    composition_effect: float
    unexplained_interaction: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_1_label": self.period_1_label,
            "period_2_label": self.period_2_label,
            "period_1_maker_ratio": round(self.period_1_maker_ratio, 4),
            "period_2_maker_ratio": round(self.period_2_maker_ratio, 4),
            "total_change": round(self.total_change, 4),
            "intra_symbol_effect": round(self.intra_symbol_effect, 4),
            "composition_effect": round(self.composition_effect, 4),
            "unexplained_interaction": round(self.unexplained_interaction, 4),
        }


class CompositionAnalyzer:
    """Decomposes aggregate behavioral metric shift into within-symbol and across-symbol components."""

    @staticmethod
    def decompose(
        period_1_weights: Mapping[str, float],
        period_1_rates: Mapping[str, float],
        period_2_weights: Mapping[str, float],
        period_2_rates: Mapping[str, float],
        p1_label: str = "2020",
        p2_label: str = "2021",
    ) -> CompositionDecomposition:
        """
        Oaxaca-Blinder / Kitagawa decomposition:
        R2 - R1 = sum(w2 * r2) - sum(w1 * r1)
                = sum(w1 * (r2 - r1))  [Intra-symbol rate change]
                + sum((w2 - w1) * r1)  [Composition weight change]
                + sum((w2 - w1) * (r2 - r1)) [Interaction term]
        """
        all_symbols = sorted(set(period_1_weights.keys()) | set(period_2_weights.keys()))

        r1 = sum(period_1_weights.get(s, 0.0) * period_1_rates.get(s, 0.0) for s in all_symbols)
        r2 = sum(period_2_weights.get(s, 0.0) * period_2_rates.get(s, 0.0) for s in all_symbols)
        total_change = r2 - r1

        intra_symbol = 0.0
        composition = 0.0
        interaction = 0.0

        for s in all_symbols:
            w1 = period_1_weights.get(s, 0.0)
            w2 = period_2_weights.get(s, 0.0)
            rate1 = period_1_rates.get(s, 0.0)
            rate2 = period_2_rates.get(s, 0.0)

            intra_symbol += w1 * (rate2 - rate1)
            composition += (w2 - w1) * rate1
            interaction += (w2 - w1) * (rate2 - rate1)

        return CompositionDecomposition(
            period_1_label=p1_label,
            period_2_label=p2_label,
            period_1_maker_ratio=r1,
            period_2_maker_ratio=r2,
            total_change=total_change,
            intra_symbol_effect=intra_symbol,
            composition_effect=composition,
            unexplained_interaction=interaction,
        )


def run_full_behavioral_and_fee_analysis(
    _output_path: Path | None = None,
) -> NoReturn:
    """Refuse to generate the retired report from fabricated input series."""
    _fail_closed()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        run_full_behavioral_and_fee_analysis(args.output)
    except LegacyAnalysisDisabled as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
