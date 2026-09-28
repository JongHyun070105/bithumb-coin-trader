"""Persistent hypothesis ledger and source inventory for strategy families.

The inventory preserves historical research as provenance. It assigns no
strategy a promoted status and marks external AOA/expert ideas as
HYPOTHESIS_GENERATION_ONLY.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


class ResearchCatalogError(ValueError):
    """Raised when catalog records are invalid or immutable evidence conflicts."""


@dataclass(frozen=True, slots=True)
class HypothesisRecord:
    hypothesis_id: str
    origin: str
    description: str
    economic_rationale: str
    strategy_family: str
    required_features: tuple[str, ...]
    known_confounders: tuple[str, ...]
    falsification_criteria: tuple[str, ...]
    research_status: str
    role: str = "DEVELOPMENT_RESEARCH"
    source_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "hypothesis_id", "origin", "description", "economic_rationale",
            "strategy_family", "research_status", "role",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ResearchCatalogError(f"{name} must be a non-empty string")
        for name in (
            "required_features", "known_confounders", "falsification_criteria", "source_paths",
        ):
            value = getattr(self, name)
            if not isinstance(value, tuple) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ResearchCatalogError(f"{name} must be a tuple of non-empty strings")
        external = any(
            token in (self.origin + " " + self.role).upper()
            for token in ("AOA", "BITMEX", "EXPERT", "EXTERNAL")
        )
        if external and self.role != "HYPOTHESIS_GENERATION_ONLY":
            raise ResearchCatalogError("external/AOA hypotheses must use HYPOTHESIS_GENERATION_ONLY")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("required_features", "known_confounders", "falsification_criteria", "source_paths"):
            payload[key] = list(payload[key])
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HypothesisRecord":
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise ResearchCatalogError("hypothesis record fields do not match schema")
        tuple_fields = {
            "required_features", "known_confounders", "falsification_criteria", "source_paths",
        }
        payload = dict(value)
        for name in tuple_fields:
            item = payload[name]
            if not isinstance(item, list):
                raise ResearchCatalogError(f"{name} must be a JSON array")
            payload[name] = tuple(item)
        try:
            return cls(**payload)
        except TypeError as exc:
            raise ResearchCatalogError("hypothesis record field types are invalid") from exc


@dataclass(frozen=True, slots=True)
class CandidateFamilyRecord:
    family_id: str
    strategy_implementations: tuple[str, ...]
    configuration_notes: str
    historical_evidence: tuple[str, ...]
    current_status: str
    retest_required: bool
    source_role: str = "INTERNAL"

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.family_id, self.configuration_notes, self.current_status, self.source_role)
        ):
            raise ResearchCatalogError("candidate family identity, configuration, and status are required")
        if not isinstance(self.strategy_implementations, tuple) or not self.strategy_implementations or any(
            not isinstance(item, str) or not item.strip() for item in self.strategy_implementations
        ):
            raise ResearchCatalogError("candidate family must identify its implementation sources")
        if not isinstance(self.historical_evidence, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.historical_evidence
        ):
            raise ResearchCatalogError("historical evidence references must be non-empty")
        if not isinstance(self.retest_required, bool):
            raise ResearchCatalogError("retest_required must be a boolean")
        if self.source_role == "EXTERNAL_AOA":
            raise ResearchCatalogError("use HYPOTHESIS_GENERATION_ONLY as source_role for external AOA records")
        if self.source_role not in {"INTERNAL", "HYPOTHESIS_GENERATION_ONLY"}:
            raise ResearchCatalogError("unknown candidate family source_role")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["strategy_implementations"] = list(self.strategy_implementations)
        payload["historical_evidence"] = list(self.historical_evidence)
        return payload


class HypothesisCatalog:
    """Append-only, globally hash-chained JSONL hypothesis ledger."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def register(self, record: HypothesisRecord) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        with self._locked_fd() as descriptor:
            events = self._read_fd(descriptor)
            prior = next((event for event in events if event["record"]["hypothesis_id"] == record.hypothesis_id), None)
            if prior is not None:
                if prior["record"] == record.to_dict():
                    return prior
                raise ResearchCatalogError(f"hypothesis {record.hypothesis_id!r} is immutable once registered")
            event: dict[str, Any] = {
                "schema_version": 1,
                "sequence": len(events),
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "previous_hash": events[-1]["record_hash"] if events else "0" * 64,
                "record": record.to_dict(),
            }
            event["record_hash"] = _sha256(_canonical(event))
            encoded = (_canonical(event) + "\n").encode("utf-8")
            offset = 0
            while offset < len(encoded):
                written = os.write(descriptor, encoded[offset:])
                if written <= 0:
                    raise OSError("hypothesis catalog append made no progress")
                offset += written
            os.fsync(descriptor)
            return event

    def seed_defaults(self) -> int:
        added = 0
        for record in default_hypotheses():
            before = self.read()
            existed = any(item["record"]["hypothesis_id"] == record.hypothesis_id for item in before)
            self.register(record)
            if not existed:
                added += 1
        return added

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._locked_fd() as descriptor:
            return self._read_fd(descriptor)

    def _locked_fd(self):
        class LockedFile:
            def __init__(self, path: Path) -> None:
                self.path = path
                self.fd: int | None = None

            def __enter__(self) -> int:
                self.fd = os.open(self.path, os.O_RDWR | os.O_APPEND)
                fcntl.flock(self.fd, fcntl.LOCK_EX)
                return self.fd

            def __exit__(self, *_: Any) -> None:
                assert self.fd is not None
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)

        return LockedFile(self.path)

    @staticmethod
    def _read_fd(descriptor: int) -> list[dict[str, Any]]:
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as stream:
            lines = list(stream)
        events: list[dict[str, Any]] = []
        previous_hash = "0" * 64
        ids: set[str] = set()
        for sequence, line in enumerate(lines):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ResearchCatalogError(f"invalid hypothesis JSONL at line {sequence + 1}") from exc
            if not isinstance(event, dict) or not isinstance(event.get("record"), dict):
                raise ResearchCatalogError(f"hypothesis catalog line {sequence + 1} must be an object")
            record = HypothesisRecord.from_dict(event["record"])
            if record.hypothesis_id in ids:
                raise ResearchCatalogError(f"duplicate hypothesis id in catalog: {record.hypothesis_id}")
            supplied_hash = event.get("record_hash")
            unsigned = {key: value for key, value in event.items() if key != "record_hash"}
            if (
                event.get("schema_version") != 1
                or event.get("sequence") != sequence
                or event.get("previous_hash") != previous_hash
                or supplied_hash != _sha256(_canonical(unsigned))
            ):
                raise ResearchCatalogError(f"hypothesis catalog hash chain invalid at line {sequence + 1}")
            ids.add(record.hypothesis_id)
            previous_hash = supplied_hash
            events.append(event)
        return events


def export_candidate_families(path: Path) -> str:
    """Write the versioned static source inventory once and return its digest."""
    payload = {
        "schema_version": 1,
        "catalog_status": "INVENTORY_ONLY_NO_PROMOTIONS",
        "families": [record.to_dict() for record in default_candidate_families()],
    }
    encoded = (_canonical(payload) + "\n").encode("utf-8")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != encoded:
            raise ResearchCatalogError(f"refusing to overwrite candidate family evidence: {destination}")
        return _sha256(encoded)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("candidate-family snapshot write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return _sha256(encoded)


def default_candidate_families() -> tuple[CandidateFamilyRecord, ...]:
    retest = "UNTESTED_BY_GOVERNED_RUNNER_REQUIRES_RETEST"
    return (
        CandidateFamilyRecord(
            "baseline_controls",
            ("builtin_strategy_id=cash", "builtin_strategy_id=buy_and_hold", "builtin_strategy_id=randomized_placebo"),
            "Zero exposure, full exposure, and timestamp-seeded randomized exposure controls.",
            ("docs/GOVERNED_RESEARCH_RUNNER.md",),
            "CONTROL_ONLY_NO_CANDIDATE_PROMOTION",
            True,
        ),
        CandidateFamilyRecord(
            "builtin_sma_trend_example",
            ("builtin_strategy_id=sma_trend",),
            "Allowlisted causal close/SMA plus lookback-return rule used to exercise the generic runner.",
            ("docs/GOVERNED_RESEARCH_RUNNER.md",),
            "ENGINE_FIXTURE_ONLY_NOT_A_PROJECT_CANDIDATE",
            True,
        ),
        CandidateFamilyRecord(
            "daily_weekly_trend_and_momentum",
            (
                "bithumb_coin_trader.daily_strategy_candidates:WeeklyAbsoluteMomentumStrategy",
                "bithumb_coin_trader.daily_strategy_candidates:WeeklySmaCrossStrategy",
                "bithumb_coin_trader.daily_strategy_candidates:WeeklyDonchianStrategy",
                "bithumb_coin_trader.daily_strategy_candidates:WeeklyDualMomentumStrategy",
            ),
            "KST completed daily/weekly trend, Donchian, and absolute/relative momentum rules.",
            ("docs/CANDIDATE_RESEARCH_2026-08-12.md", "docs/STRATEGY_V2_RESEARCH_2026-08-25.md"),
            "HISTORICAL_EXPLORATORY_NO_SELECTED_CANDIDATE",
            True,
        ),
        CandidateFamilyRecord(
            "wave4_daily_momentum_and_volume_clock",
            (
                "bithumb_coin_trader.wave4:DailyTsmom84Strategy",
                "bithumb_coin_trader.wave4:DailyTsmom84Rv20MedianGateStrategy",
                "bithumb_coin_trader.wave4:VolumeClockFirstLastMomentumStrategy",
            ),
            "Train-only 84-day momentum, realized-volatility median gate, and volume-clock hypothesis.",
            ("docs/CANDIDATE_RESEARCH_2026-08-14.md",),
            "HISTORICAL_REJECTED_OR_INSUFFICIENT_SAMPLE",
            True,
        ),
        CandidateFamilyRecord(
            "v2_daily_absolute_momentum",
            (
                "bithumb_coin_trader.daily_strategy_candidates:daily_candidate_factories",
                "bithumb_coin_trader.strategy_v2_research:build_strategy_v2_report",
            ),
            "126/63 daily/weekly absolute momentum; cash remained the operational choice.",
            ("docs/STRATEGY_V2_RESEARCH_2026-08-25.md",),
            "RESEARCH_CANDIDATE_ONLY_CAN_PROMOTE_FALSE",
            True,
        ),
        CandidateFamilyRecord(
            "v3_daily_target_weight",
            (
                "bithumb_coin_trader.strategy_v3_candidates:E9DonchianVolatilityStrategy",
                "bithumb_coin_trader.strategy_v3_candidates:EntryVolatilityAbsoluteMomentumStrategy",
                "bithumb_coin_trader.strategy_v3_candidates:MajorityTrendStrategy",
            ),
            "Daily Donchian, volatility/absolute-momentum, and majority-trend target weights.",
            ("docs/STRATEGY_V4_AUDIT_2026-08-25.md",),
            retest,
            True,
        ),
        CandidateFamilyRecord(
            "v4_v4b_regime_breakout_and_trend",
            (
                "bithumb_coin_trader.strategy_v4_candidates:V4TrendVolatilityRegimeStrategy",
                "bithumb_coin_trader.strategy_v4_candidates:V4AdaptiveDonchianAtrStrategy",
                "bithumb_coin_trader.strategy_v4_candidates:V4KamaTrendStrategy",
                "bithumb_coin_trader.strategy_v4_candidates:V4TripleMomentumFilterStrategy",
                "bithumb_coin_trader.strategy_v4_candidates:V4AdxKamaConfluenceStrategy",
                "bithumb_coin_trader.strategy_v4_candidates:V4VolatilityAdjustedMomentumStrategy",
                "bithumb_coin_trader.strategy_v4b_candidates:V452WeekHighBreakoutStrategy",
                "bithumb_coin_trader.strategy_v4b_candidates:V4TrendQualityFilterStrategy",
            ),
            "Regime, volatility sizing, Donchian/52-week breakouts, KAMA, and trend filters.",
            ("docs/STRATEGY_V4_RESEARCH_2026-08-25.md", "docs/STRATEGY_V4_AUDIT_2026-08-25.md"),
            "HISTORICAL_RESEARCH_FINALIST_UNTRUSTED_PENDING_GOVERNED_RETEST",
            True,
        ),
        CandidateFamilyRecord(
            "v5_regime_dual_momentum_pullback",
            (
                "bithumb_coin_trader.strategy_v5_candidates:V5RegimeAdaptiveDonchianStrategy",
                "bithumb_coin_trader.strategy_v5_candidates:V5CrossAssetDualMomentumStrategy",
                "bithumb_coin_trader.strategy_v5_candidates:V5TrendPullbackStrategy",
            ),
            "Preregistered V4 champion challengers; includes multi-asset data requirements.",
            ("docs/STRATEGY_V5_PREREGISTRATION_2026-08-25.md", "docs/STRATEGY_V5_1_VALIDATION_AUDIT_2026-08-25.md"),
            retest,
            True,
        ),
        CandidateFamilyRecord(
            "v6_satellite_and_core_satellite",
            (
                "bithumb_coin_trader.strategy_v6_candidates:V6FastDonchianSwingStrategy",
                "bithumb_coin_trader.strategy_v6_candidates:V6DailyEmaPullbackStrategy",
                "bithumb_coin_trader.strategy_v6_candidates:V6CrossAssetFastRotationStrategy",
                "bithumb_coin_trader.composite_portfolio_backtest:run_composite_portfolio_backtest",
            ),
            "Satellite rules and V4 Core + Satellite allocation; historical metric quoted as +48.43%; composite uses the shared rebalance engine.",
            ("docs/STRATEGY_V6_PORTFOLIO_AUDIT_2026-08-25.md", "docs/BACKTEST_AUTHORITY_AND_COST_CONTRACT.md"),
            "HISTORICAL_QUOTED_+48.43_SUPPORTING_RESULT_MISSING_UNTRUSTED",
            True,
        ),
        CandidateFamilyRecord(
            "wave5_four_hour_pullback",
            ("bithumb_coin_trader.wave5:FourHourTrendPullbackStrategy",),
            "Four-hour trend pullback implementation; historical research remains exploratory until retested through the governed interface.",
            ("docs/CANDIDATE_RESEARCH_2026-08-14.md",),
            retest,
            True,
        ),
        CandidateFamilyRecord(
            "v7_multi_asset_and_intraday",
            (
                "bithumb_coin_trader.strategy_v7_candidates:V7MultiTimeframeTrendPullbackStrategy",
                "bithumb_coin_trader.strategy_v7_candidates:V7VolatilityContractionBreakoutStrategy",
                "bithumb_coin_trader.strategy_v7_candidates:V7ShortTermMeanReversionStrategy",
                "bithumb_coin_trader.strategy_v7_candidates:V7CrossSectionalIntradayRotationStrategy",
            ),
            "Daily/intraday long-only candidates; cross-sectional portfolio claims require shared-cash accounting.",
            ("docs/STRATEGY_V7_1_AUDIT_INVALIDATION_2026-08-25.md",),
            "V7_1_HISTORICAL_REPORT_INVALIDATED_PRESERVE_AND_RETEST",
            True,
        ),
        CandidateFamilyRecord(
            "v8_cross_sectional_intraday",
            (
                "bithumb_coin_trader.strategy_v8_candidates:V8CrossSectionalMomentumStrategy",
                "bithumb_coin_trader.strategy_v8_candidates:V8VolatilityBreakoutStrategy",
                "bithumb_coin_trader.strategy_v8_candidates:V8MarketRelativeStrengthStrategy",
                "bithumb_coin_trader.strategy_v8_candidates:V8TrendAlignedReversalStrategy",
                "bithumb_coin_trader.multi_asset_backtest:MultiAssetSharedCashBacktester",
            ),
            "Cross-sectional multi-asset hourly-entry/four-hour-context strategies with shared cash.",
            ("docs/STRATEGY_V8_RESEARCH_2026-08-25.md", "docs/STRATEGY_V8_1_ROBUSTNESS_2026-08-25.md"),
            "V8_1_LOAO_FAIL_AND_FAMILY_DROPPED_HISTORICALLY_RETEST_REQUIRED",
            True,
        ),
        CandidateFamilyRecord(
            "opportunity_breakout_rebound",
            (
                "bithumb_coin_trader.opportunity_candidates:DonchianRetestCandidate",
                "bithumb_coin_trader.opportunity_candidates:DualMomentumCandidate",
                "bithumb_coin_trader.opportunity_candidates:ExtremeDropReboundCandidate",
            ),
            "Donchian retest, dual momentum, and extreme-drop rebound hypotheses.",
            ("docs/OPPORTUNITY_RESEARCH_2026-08-25.md",),
            "DEVELOPMENT_AND_HISTORICAL_HOLDOUT_ALREADY_USED_NO_REOPEN",
            True,
        ),
        CandidateFamilyRecord(
            "winrate_mean_reversion_trend_volatility_session_meta",
            (
                "bithumb_coin_trader.winrate_mean_reversion_candidates:SelectiveMeanReversionStrategy",
                "bithumb_coin_trader.winrate_trend_candidates:SelectiveTrendCandidate",
                "bithumb_coin_trader.winrate_volatility_candidates:VolatilityBreakoutStrategy",
                "bithumb_coin_trader.winrate_session_candidates:SessionVolumeVwapStrategy",
                "bithumb_coin_trader.winrate_meta_candidates:CausalOnlineMetaStrategy",
            ),
            "Selective mean-reversion, trend, volatility, session-volume and causal meta selectors.",
            ("docs/CANDIDATE_RESEARCH_2026-08-11.md", "docs/OPPORTUNITY_RESEARCH_2026-08-25.md"),
            "HISTORICAL_EXPLORATORY_REQUIRES_UNIFIED_RETEST",
            True,
        ),
        CandidateFamilyRecord(
            "legacy_rule_and_live_policy_strategies",
            (
                "bithumb_coin_trader.strategy:TimeSeriesMomentumStrategy",
                "bithumb_coin_trader.strategy:CompletedIntervalStrategy",
                "bithumb_coin_trader.strategy:IntersectionLongStrategy",
                "bithumb_coin_trader.strategy:MajorityVoteLongStrategy",
                "bithumb_coin_trader.strategy:CompletedCalendarMonthStrategy",
                "bithumb_coin_trader.strategy:DailySmaTrendStrategy",
                "bithumb_coin_trader.strategy:DailyCloseAboveSmaStrategy",
                "bithumb_coin_trader.strategy:DonchianBreakoutStrategy",
                "bithumb_coin_trader.strategy:TradingRangeBreakoutStrategy",
                "bithumb_coin_trader.strategy:DailySmaAdxTrendStrategy",
                "bithumb_coin_trader.strategy:DailyMacdPvoTrendStrategy",
                "bithumb_coin_trader.strategy:DCBollingerRsiArmedReentryStrategy",
                "bithumb_coin_trader.strategy:BollingerRsiReentryStrategy",
                "bithumb_coin_trader.strategy:BollingerRsiUptrendReentryStrategy",
                "bithumb_coin_trader.strategy:BollingerRsiFourHourUptrendReentryStrategy",
                "bithumb_coin_trader.strategy:BollingerSqueezeBreakoutStrategy",
                "bithumb_coin_trader.strategy:TrendBreakoutStrategy",
                "bithumb_coin_trader.strategy:InstitutionalDisplacementStrategy",
                "bithumb_coin_trader.strategy:TradingAgentsMultiAgentStrategy",
                "bithumb_coin_trader.wave5:FourHourTrendPullbackStrategy",
                "bithumb_coin_trader.wave4:VolumeClockFirstLastMomentumStrategy",
            ),
            "Legacy implementations stay available for reproduction; adapt to target-weight and governed costs before promotion.",
            ("docs/BACKTEST_AUTHORITY_AND_COST_CONTRACT.md", "docs/CANDIDATE_RESEARCH_2026-08-14.md"),
            retest,
            True,
        ),
        CandidateFamilyRecord(
            "microstructure_h1_h5",
            ("bithumb_coin_trader.research_infra.hypotheses:register_default_hypotheses", "bithumb_coin_trader.research_infra.execution:ResearchExecutionSimulator"),
            "Book/trade event hypotheses require book-level execution and data quality; candle backtests are not equivalent.",
            ("docs/research-infra/README.md", "docs/MICROSTRUCTURE_RESEARCH_PREREGISTRATION_V1.md"),
            "EXPLORATORY_ONLY_NO_ALPHA_PROMOTION",
            True,
        ),
        CandidateFamilyRecord(
            "external_aoa_expert_ideas",
            ("UNKNOWN_SOURCE_NOT_PRESENT_IN_AUTHORITATIVE_TREE",),
            "The current branch mentions AOA/expert-origin ideas but contains no exact source record to reconstruct.",
            ("docs/project-readiness-20260927.md", "docs/post-30h-execution-playbook.md"),
            "SOURCE_NOT_PRESENT_INVENTORY_PLACEHOLDER_ONLY",
            True,
            "HYPOTHESIS_GENERATION_ONLY",
        ),
    )


def default_hypotheses() -> tuple[HypothesisRecord, ...]:
    status = "UNTESTED_OR_HISTORICAL_EXPLORATORY_REQUIRES_GOVERNED_RETEST"
    return (
        HypothesisRecord("H1", "internal_microstructure", "Order-book imbalance predicts short-term mid-price movement.", "Persistent depth asymmetry may reflect short-lived supply/demand pressure.", "microstructure_h1_h5", ("depth_imbalance_l1", "depth_imbalance_l5", "qi_l1", "qi_l5"), ("queue position is unobserved", "stale or sparse book snapshots", "spread and taker costs can exceed the signal"), ("purged out-of-sample IC is non-positive", "net executable return fails conservative costs", "effect disappears across markets or regimes"), status, source_paths=("src/bithumb_coin_trader/research_infra/hypotheses.py",)),
        HypothesisRecord("H2", "internal_microstructure", "Signed aggressive trade flow predicts short-term continuation, with possible exhaustion at extremes.", "Persistent informed flow can move prices until liquidity absorbs it.", "microstructure_h1_h5", ("ati_5s", "ati_30s", "signed_volume_30s", "trade_count_30s"), ("trade sign classification error", "trade clustering and autocorrelation", "fees, spread and latency"), ("sign is unstable across folds", "IC fails after latency-aligned execution costs", "extreme-flow reversal is not separately repeatable"), status, source_paths=("src/bithumb_coin_trader/research_infra/hypotheses.py",)),
        HypothesisRecord("H3", "internal_microstructure", "Microprice displacement predicts near-term price movement.", "Size-weighted midpoint displacement may indicate asymmetric displayed liquidity.", "microstructure_h1_h5", ("microprice_bias_bps", "microprice_displacement"), ("displayed size cancels", "book sampling lag", "spread and depth costs"), ("prediction is non-positive after purging", "effect fails in held-out volatility regimes", "depth-aware net returns are non-positive"), status, source_paths=("src/bithumb_coin_trader/research_infra/hypotheses.py",)),
        HypothesisRecord("H4", "internal_cross_exchange", "Binance or Upbit price changes lead subsequent Bithumb price changes at short horizons.", "More liquid venues may incorporate global information earlier.", "microstructure_h1_h5", ("cross_exchange_return_diff_binance_5s", "cross_exchange_return_diff_upbit_5s", "cross_exchange_basis_binance", "cross_exchange_basis_upbit"), ("clock skew", "venue outages", "non-synchronous trades", "transfer and basis constraints"), ("lead vanishes after timestamp alignment", "out-of-sample conditional IC is non-positive", "net executable return fails venue and latency costs"), status, source_paths=("src/bithumb_coin_trader/research_infra/hypotheses.py",)),
        HypothesisRecord("H5", "internal_cross_exchange", "Cross-exchange basis shocks either revert or transmit into Bithumb, conditional on regime.", "A local dislocation may mean temporary flow imbalance or a global repricing event.", "microstructure_h1_h5", ("cross_exchange_basis_binance", "cross_exchange_basis_upbit", "market_regime"), ("basis definition and FX alignment", "venue-specific funding or withdrawal frictions", "regime boundary fitted with future data"), ("both conditional signs are unstable", "conditional effect fails purged validation", "execution costs remove any net edge"), status, source_paths=("src/bithumb_coin_trader/research_infra/hypotheses.py",)),
        HypothesisRecord("H-ABS-MOMENTUM", "internal_strategy_research", "Long-only absolute momentum can improve risk-adjusted BTC exposure versus continuous holding.", "Trend persistence may justify exposure only when trailing return is positive.", "daily_weekly_trend_and_momentum", ("completed_daily_close", "trailing_return_63_126d"), ("market regime selection", "small trade sample", "parameter reuse across reports"), ("purged fold net returns fail cash and buy-and-hold comparison", "stress costs reverse results", "performance is concentrated in one trade or fold"), status, source_paths=("docs/STRATEGY_V2_RESEARCH_2026-08-25.md", "docs/CANDIDATE_RESEARCH_2026-08-12.md")),
        HypothesisRecord("H-MEAN-REVERSION", "internal_strategy_research", "A completed-bar deviation from a price band followed by re-entry predicts short-horizon reversal.", "Temporary order-flow pressure may overshoot and revert when the initiating flow fades.", "winrate_mean_reversion_trend_volatility_session_meta", ("completed_30m_close", "bollinger_band_position", "rsi"), ("trend continuation outside bands", "intrabar ordering unavailable", "low trade counts", "selection among many correlated variants"), ("costed net return is not positive across folds", "return depends on final liquidation", "placebo performs similarly"), status, source_paths=("docs/CANDIDATE_RESEARCH_2026-08-11.md", "docs/OPPORTUNITY_RESEARCH_2026-08-25.md")),
        HypothesisRecord("H-VOL-BREAKOUT", "internal_strategy_research", "Volatility contraction followed by a completed-bar range breakout predicts continuation.", "Compressed ranges can precede repricing as new information is incorporated.", "v4_v4b_regime_breakout_and_trend", ("completed_bar_range", "realized_volatility", "donchian_channel"), ("breakout whipsaw", "survivorship and asset-universe drift", "gap and fill assumptions"), ("net results are non-positive under conservative costs", "fold signs are inconsistent", "edge vanishes after purged feature selection"), status, source_paths=("docs/CANDIDATE_RESEARCH_2026-08-12.md", "docs/STRATEGY_V4_RESEARCH_2026-08-25.md")),
        HypothesisRecord("H-REGIME-ALLOCATION", "internal_preregistered_research", "Regime-conditioned exposure can reduce drawdown without giving up too much return versus fixed exposure.", "Lower exposure in bear or crash regimes may reduce adverse compounding.", "v5_regime_dual_momentum_pullback", ("completed_close_vs_sma200", "momentum_90d", "realized_volatility_30d"), ("regime boundary selection", "rare crisis sample", "lookback overlap and label leakage"), ("walk-forward exposure rule does not reduce MDD at comparable net return", "regime classifications use future data", "stress costs invalidate benefit"), status, source_paths=("docs/STRATEGY_V5_PREREGISTRATION_2026-08-25.md",)),
        HypothesisRecord("H-CROSS-ASSET-MOMENTUM", "internal_strategy_research", "Risk-adjusted relative momentum among eligible spot assets improves portfolio outcomes versus single-asset exposure.", "Relative strength can shift capital toward stronger assets while absolute momentum gates keep cash as an option.", "v5_regime_dual_momentum_pullback", ("asset_trailing_return", "realized_volatility", "point_in_time_eligible_universe"), ("survivorship bias", "listing history and shared-cash constraints", "cross-asset correlation shifts"), ("leave-one-asset-out results fail", "shared-cash return trails baselines", "eligibility uses future asset availability"), status, source_paths=("docs/STRATEGY_V5_PREREGISTRATION_2026-08-25.md", "docs/STRATEGY_V8_1_ROBUSTNESS_2026-08-25.md")),
        HypothesisRecord("H-TREND-PULLBACK", "internal_strategy_research", "Pullback entries within an established long-term uptrend improve entry cost relative to buying breakouts.", "A temporary retracement can offer a lower-cost entry while the primary trend remains intact.", "v5_regime_dual_momentum_pullback", ("completed_close_vs_sma200", "rsi14", "ema20", "prior_high"), ("trend filter lookahead", "mean-reversion entry fights regime change", "intrabar stop ordering"), ("entry does not improve net return or drawdown out of sample", "benefit fails stress costs", "results hinge on a single regime"), status, source_paths=("docs/STRATEGY_V5_PREREGISTRATION_2026-08-25.md", "src/bithumb_coin_trader/strategy_v6_candidates.py")),
        HypothesisRecord("H-CORE-SATELLITE", "internal_strategy_research", "A lower-frequency trend core plus a limited swing satellite can improve portfolio return or drawdown over either leg alone.", "Distinct entry horizons may diversify exposure timing, subject to additive exposure and shared cash constraints.", "v6_satellite_and_core_satellite", ("core_target_weight", "satellite_target_weight", "shared_cash_equity"), ("overlapping signals", "few round trips", "weight normalization and component cancellation", "historic trial selection"), ("combined portfolio fails fold-wise comparison with both legs and cash", "exposure or fees do not reconcile to fills", "benefit disappears under cost stress"), status, source_paths=("docs/STRATEGY_V6_PORTFOLIO_AUDIT_2026-08-25.md",)),
        HypothesisRecord("H-RELATIVE-STRENGTH-ROTATION", "internal_strategy_research", "Among point-in-time eligible assets, recent relative strength may concentrate exposure in leading markets.", "Cross-sectional winners may continue while a broad trend gate limits bear-market exposure.", "v8_cross_sectional_intraday", ("point_in_time_asset_universe", "relative_strength_rank", "broad_market_trend"), ("asset survivorship", "concentration in one asset", "cross-sectional price asynchrony", "shared-cash liquidity"), ("leave-one-asset-out results fail", "rank is unstable across folds", "conservative shared-cash results trail baselines"), status, source_paths=("docs/STRATEGY_V8_RESEARCH_2026-08-25.md", "docs/STRATEGY_V8_1_ROBUSTNESS_2026-08-25.md")),
        HypothesisRecord("H-SESSION-VOLUME", "internal_strategy_research", "Time-of-day volume patterns may identify recurring windows of liquidity and directional information.", "Repeated intraday participation patterns can shape the timing and cost of price discovery.", "winrate_mean_reversion_trend_volatility_session_meta", ("completed_trade_volume_by_session", "point_in_time_session_id"), ("UTC/KST daylight and session mapping", "volume lookahead", "exchange-specific calendar effects"), ("session selection fails training-only fit", "pattern does not persist across chronological folds", "costed returns do not exceed time-random placebo"), status, source_paths=("src/bithumb_coin_trader/winrate_session_candidates.py", "docs/CANDIDATE_RESEARCH_2026-08-14.md")),
        HypothesisRecord("H-OPPORTUNITY-SHOCK", "internal_strategy_research", "Rare severe drops followed by a completed-bar recovery may produce asymmetric long-only rebound opportunities.", "Liquidity shocks can overshoot fundamental or market-wide repricing and partly mean-revert.", "opportunity_breakout_rebound", ("completed_bar_drawdown", "recovery_confirmation", "market_regime"), ("sparse event count", "intrabar ordering", "survivorship and sample selection", "historical holdout already observed"), ("frozen rule fails a newly authorized retrospective sample", "gains depend on forced terminal liquidation", "stress execution erases expectancy"), status, source_paths=("docs/OPPORTUNITY_RESEARCH_2026-08-25.md",)),
        HypothesisRecord("H-META-SELECTOR", "internal_strategy_research", "A causal online selector may improve outcome by adapting between fixed base strategies using only prior evidence.", "Conditional strategy performance can vary by regime, but selection itself must be fitted without future data.", "winrate_mean_reversion_trend_volatility_session_meta", ("causal_strategy_state", "past_only_performance", "point_in_time_regime"), ("small effective sample", "winner's curse", "selector feedback and non-stationarity"), ("nested walk-forward selector fails cash/base strategy", "parameter or model updates use validation outcomes", "placebo selector performs similarly"), status, source_paths=("src/bithumb_coin_trader/winrate_meta_candidates.py", "docs/OPPORTUNITY_RESEARCH_2026-08-25.md")),
        HypothesisRecord("H-AOA-EXPERT-UNKNOWN", "AOA_EXTERNAL_SOURCE_NOT_PRESENT", "Handoff context references AOA/expert-generated ideas, but their exact records are absent from this checkout.", "UNKNOWN: recover the source record before making a more specific economic claim.", "external_aoa_expert_ideas", ("UNKNOWN_SOURCE_FEATURES" ,), ("unverified authorship and provenance", "external data contamination", "selection and survivorship bias"), ("do not register as an executable candidate until the source is recovered and preregistered", "never use AOA-derived records to promote a candidate"), "SOURCE_NOT_PRESENT", role="HYPOTHESIS_GENERATION_ONLY", source_paths=("docs/project-readiness-20260927.md", "docs/post-30h-execution-playbook.md")),
    )


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ResearchCatalogError("catalog records must be finite canonical JSON data") from exc


def _sha256(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()
