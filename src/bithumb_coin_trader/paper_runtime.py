"""Caller-fed, local-only runtime joining frozen research to the paper ledger.

The runtime accepts normalized public candles and order-book snapshots. It has
no exchange client and no private endpoint path. A caller supplies the public
feed; fills use visible book depth, configured latency, taker fees, tick/lot
rounding, and an explicit partial-depth policy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Mapping, Sequence

from .execution_simulator import (
    DeterministicTakerSimulator,
    ExecutionFill,
    ExecutionResult,
    MarketOrderRequest,
    OrderBookSnapshot,
)
from .models import Candle
from .paper_engine import OrderStatus, PaperEngineError, PaperOrder, PaperPortfolio
from .paper_journal import PaperEventJournal, PaperJournalIntegrityError
from .research_infra.costs import SpotCostScenario, spot_execution_price
from .research_infra.paper_readiness import _check_candidate
from .research_infra.builtin_strategies import (
    create_builtin_strategy,
    governed_candidate_strategy_ids,
    strategy_source_modules,
)
from .research_infra.walk_forward_runner import FittedTargetWeightStrategy
from .risk_engine import RiskEngine, RiskVerdict


class PaperRuntimeError(ValueError):
    """Raised when a local paper event violates runtime or safety contracts."""


@dataclass(frozen=True, slots=True)
class NormalizedPaperEvent:
    event_id: str
    received_at_ms: int
    candle: Candle | None
    orderbooks: tuple[OrderBookSnapshot, ...]
    source_observation_sha256: tuple[str, ...] = ()


class PaperRuntime:
    """Process deterministic paper events against a durable local ledger."""

    def __init__(
        self,
        *,
        candidate_artifact: Mapping[str, Any],
        experiment_metrics_path: Path,
        strategy: FittedTargetWeightStrategy,
        warmup_candles: Sequence[Candle],
        cost_scenario: SpotCostScenario,
        accept_depth_partials: bool,
        journal: PaperEventJournal,
        risk_engine: RiskEngine,
        initial_cash_krw: Decimal,
        market: str,
    ) -> None:
        if not market.startswith("KRW-"):
            raise PaperRuntimeError("paper runtime only supports KRW spot markets")
        if not isinstance(accept_depth_partials, bool):
            raise PaperRuntimeError("accept_depth_partials must be explicit")
        if cost_scenario.latency_ms <= 0:
            raise PaperRuntimeError("paper fill execution requires an explicit positive latency assumption")
        if cost_scenario.taker_fee_bps <= 0 or cost_scenario.slippage_bps <= 0:
            raise PaperRuntimeError("paper fills require explicit positive taker-fee and slippage assumptions")
        if not initial_cash_krw.is_finite() or initial_cash_krw <= 0:
            raise PaperRuntimeError("paper account initial cash must be finite and positive")
        candidate = candidate_artifact.get("candidate")
        if not isinstance(candidate, dict) or candidate.get("schema_version") != 2:
            raise PaperRuntimeError("runtime requires a governed v2 frozen candidate artifact")
        if candidate.get("strategy_id") not in governed_candidate_strategy_ids():
            raise PaperRuntimeError("frozen strategy has no paper runtime adapter")
        if candidate.get("candidate_id") != candidate.get("experiment_id"):
            raise PaperRuntimeError("candidate and experiment identity differ")
        if candidate.get("dataset_roles") != ["DEVELOPMENT_EXPLORATORY"]:
            raise PaperRuntimeError("paper runtime rejects non-development candidate data")
        _validate_paper_cost_scenario(candidate, cost_scenario)
        if candidate.get("market") not in (None, market):
            raise PaperRuntimeError("frozen candidate market differs from runtime market")
        metrics_path = Path(experiment_metrics_path)
        if metrics_path.is_symlink() or not metrics_path.is_file():
            raise PaperRuntimeError("frozen experiment metrics must be a regular file")
        metrics_bytes = metrics_path.read_bytes()
        metrics_sha = hashlib.sha256(metrics_bytes).hexdigest()
        eligibility = _check_candidate(
            dict(candidate_artifact),
            None,
            {"experiment_metrics_sha256": metrics_sha},
        )
        if eligibility["status"] != "PASS":
            raise PaperRuntimeError(f"frozen candidate verification failed: {eligibility['reason']}")
        self._candidate = candidate
        self._candidate_id = str(candidate["candidate_id"])
        self._strategy = strategy
        self._journal = journal
        self._halt_marker_path = Path(f"{journal.path}.halt.json")
        self._journal_failed = False
        self._risk = risk_engine
        self._scenario = cost_scenario
        self._accept_depth_partials = accept_depth_partials
        self._market = market
        self._validate_frozen_strategy(metrics_bytes, warmup_candles)

        portfolio = self._journal.initialize_portfolio(
            PaperPortfolio(cash_krw=initial_cash_krw)
        )
        self._portfolio = portfolio
        loaded = self._journal.load_runtime_state()
        warmup_payload = [_candle_dict(candle) for candle in warmup_candles[-2_000:]]
        warmup_hash = _hash_json(warmup_payload)
        self._state: dict[str, Any] = loaded or self._new_state()
        marker_reason = self._read_halt_marker()
        if marker_reason is not None:
            self._state["halted"] = True
            self._state["halt_reason"] = marker_reason
        if self._state.get("candidate_id") != self._candidate_id or self._state.get("market") != market:
            raise PaperJournalIntegrityError("persisted paper runtime belongs to another candidate or market")
        if loaded is None:
            self._state["candles"] = warmup_payload
            self._state["warmup_sha256"] = warmup_hash
        elif self._state.get("warmup_sha256") != warmup_hash:
            raise PaperJournalIntegrityError("restart warmup candles differ from the durable paper history")
        self._risk.halted = bool(self._state.get("halted", False))
        self._risk.halt_reason = str(self._state.get("halt_reason", ""))
        self._risk.consecutive_rejections = int(self._state.get("consecutive_failures", 0))
        self._state["restart_count"] = int(self._state.get("restart_count", 0)) + 1
        self._state["pending_orders"] = self._pending_from_journal()
        self._journal.save_runtime_state(self._state)
        if not self._state.get("halted"):
            self._replay_incomplete_events()
        open_ids = {order.order_id for order in self._journal.load_open_orders()}
        unresolved = open_ids - set(self._state.get("pending_orders", {}))
        if unresolved:
            self._persist_halt("OPEN_ORDER_RECOVERY_METADATA_MISSING")
            raise PaperJournalIntegrityError("open paper orders remain outside the recovered runtime state")

    @classmethod
    def from_frozen_artifact(
        cls,
        *,
        candidate_artifact: Mapping[str, Any],
        research_root: Path,
        warmup_candles: Sequence[Candle],
        cost_scenario: SpotCostScenario,
        accept_depth_partials: bool,
        journal: PaperEventJournal,
        risk_engine: RiskEngine,
        initial_cash_krw: Decimal,
        market: str,
    ) -> "PaperRuntime":
        candidate = candidate_artifact.get("candidate")
        if not isinstance(candidate, dict):
            raise PaperRuntimeError("frozen candidate record is missing")
        metrics_path = (Path(research_root) / str(candidate.get("metrics_relative_path", ""))).resolve()
        root = Path(research_root).resolve()
        if root not in metrics_path.parents:
            raise PaperRuntimeError("frozen metrics path escapes the research root")
        metrics = _load_json_object(metrics_path)
        config = candidate.get("strategy_config")
        parameters = config.get("parameters") if isinstance(config, dict) else None
        if not isinstance(parameters, dict):
            raise PaperRuntimeError("frozen strategy parameters are missing")
        strategy_definition = create_builtin_strategy(
            str(candidate.get("strategy_id")), int(candidate.get("seed", -1)), parameters
        )
        strategy = strategy_definition.fit(tuple(warmup_candles))
        expected = _frozen_parameter_hash(metrics)
        actual = _hash_json(dict(strategy.parameters()))
        if actual != expected:
            raise PaperRuntimeError("runtime strategy parameters differ from the frozen walk-forward manifest")
        if _current_strategy_source_hash(str(candidate["strategy_id"])) != candidate.get("strategy_source_sha256"):
            raise PaperRuntimeError("runtime strategy source differs from the frozen code hash")
        return cls(
            candidate_artifact=candidate_artifact,
            experiment_metrics_path=metrics_path,
            strategy=strategy,
            warmup_candles=warmup_candles,
            cost_scenario=cost_scenario,
            accept_depth_partials=accept_depth_partials,
            journal=journal,
            risk_engine=risk_engine,
            initial_cash_krw=initial_cash_krw,
            market=market,
        )

    def process_event(self, event: NormalizedPaperEvent) -> dict[str, Any]:
        try:
            return self._process_event(event)
        except Exception as exc:
            if isinstance(exc, (PaperJournalIntegrityError, OSError, sqlite3.Error)):
                self._journal_failed = True
                reason = "PAPER_JOURNAL_FAILURE"
            elif isinstance(exc, PaperEngineError):
                reason = "PAPER_ACCOUNTING_INVARIANT_FAILURE"
            else:
                reason = str(self._state.get("halt_reason") or "PAPER_RUNTIME_FAILURE")
            self._persist_halt(reason)
            raise

    def _process_event(self, event: NormalizedPaperEvent) -> dict[str, Any]:
        """Process one completed candle or public book update idempotently."""
        self._validate_event(event)
        event_hash = _hash_json(_event_dict(event))
        prior = self._journal.get_runtime_event(event.event_id)
        if prior is not None:
            if prior[0] != event_hash:
                raise PaperJournalIntegrityError("market event id was reused with different data")
            return prior[1]
        self._journal.record_incoming_runtime_event(
            event.event_id, event_hash, event.received_at_ms, _event_dict(event)
        )
        last_timestamp = self._state.get("last_event_timestamp_ms")
        if last_timestamp is not None and event.received_at_ms <= int(last_timestamp):
            self._persist_halt("OUT_OF_ORDER_MARKET_EVENT")
            raise PaperRuntimeError("market events must be strictly chronological")

        self._portfolio = self._journal.load_portfolio()
        latest_book = event.orderbooks[-1]
        self._state["market_data_age_ms"] = max(0, event.received_at_ms - _book_timestamp_ms(latest_book))
        self._update_account_metrics(latest_book)
        health, health_reasons = self._risk.evaluate_runtime_health(
            market_data_age_ms=float(self._state["market_data_age_ms"]),
            daily_loss_fraction=float(self._state.get("daily_loss_fraction", 0.0)),
            drawdown_fraction=float(self._state.get("drawdown_fraction", 0.0)),
            journal_healthy=not self._journal_failed,
            strategy_fresh=self._state.get("strategy_state") != "STALE",
        )
        self._state["last_risk_verdict"] = health.value
        self._state["last_risk_reason_codes"] = list(health_reasons)
        if health == RiskVerdict.HALT:
            self._persist_halt(";".join(health_reasons))
        if event.candle is not None and not self._state.get("halted"):
            self._append_candle(event.candle)
            try:
                target = self._strategy.target_weight(self._candles())
            except Exception:
                self._persist_halt("STRATEGY_FAILURE")
                raise
            if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target) or not 0 <= target <= 1:
                self._persist_halt("INVALID_STRATEGY_OUTPUT")
                raise PaperRuntimeError("frozen strategy must emit a finite target weight in [0, 1]")
            self._state["signal_count"] = int(self._state.get("signal_count", 0)) + 1
            self._state["target_weight"] = float(target)
            self._state["strategy_state"] = "READY"
            self._create_target_order(float(target), event)

        result_rows: list[dict[str, Any]] = []
        if not self._state.get("halted"):
            pending = dict(self._state.get("pending_orders", {}))
            for order_id in sorted(pending):
                row = self._advance_order(order_id, pending[order_id], event)
                if row is not None:
                    result_rows.append(row)

        self._portfolio = self._journal.load_portfolio()
        self._state["last_event_timestamp_ms"] = event.received_at_ms
        self._state["market_data_age_ms"] = max(0, event.received_at_ms - _book_timestamp_ms(latest_book))
        self._update_account_metrics(latest_book)
        health, health_reasons = self._risk.evaluate_runtime_health(
            market_data_age_ms=float(self._state["market_data_age_ms"]),
            daily_loss_fraction=float(self._state.get("daily_loss_fraction", 0.0)),
            drawdown_fraction=float(self._state.get("drawdown_fraction", 0.0)),
            journal_healthy=not self._journal_failed,
            strategy_fresh=self._state.get("strategy_state") != "STALE",
        )
        if health == RiskVerdict.HALT:
            self._persist_halt(";".join(health_reasons))
        outcome = {
            "schema_version": 1,
            "event_id": event.event_id,
            "candidate_id": self._candidate_id,
            "target_weight": self._state.get("target_weight"),
            "orders": result_rows,
            "metrics": self.metrics(latest_book),
        }
        try:
            self._journal.record_runtime_event(
                event.event_id,
                event_hash,
                event.received_at_ms,
                outcome,
                self._state,
            )
        except Exception:
            self._persist_halt("PAPER_JOURNAL_FAILURE")
            raise
        return outcome

    def cancel_order(self, order_id: str, timestamp_ms: int) -> None:
        order = self._journal.load_order(order_id)
        if order.status in {OrderStatus.CANCEL_PENDING, OrderStatus.CANCELLED}:
            return
        order.transition_to(OrderStatus.CANCEL_PENDING, timestamp_ms, f"cancel:{order_id}:{timestamp_ms}")
        self._journal.update_order(order)

    def acknowledge_cancel(self, order_id: str, timestamp_ms: int) -> None:
        order = self._journal.load_order(order_id)
        if order.status == OrderStatus.CANCELLED:
            return
        order.transition_to(OrderStatus.CANCELLED, timestamp_ms, f"cancel-ack:{order_id}:{timestamp_ms}")
        self._journal.update_order(order)
        pending = dict(self._state.get("pending_orders", {}))
        pending.pop(order_id, None)
        self._state["pending_orders"] = pending
        self._journal.save_runtime_state(self._state)

    def expire_order(self, order_id: str, timestamp_ms: int, reason: str = "PAPER_ORDER_EXPIRED") -> None:
        if not reason.strip():
            raise PaperRuntimeError("order expiry reason must be non-empty")
        order = self._journal.load_order(order_id)
        if order.status == OrderStatus.EXPIRED:
            return
        order.transition_to(OrderStatus.EXPIRED, timestamp_ms, f"expire:{order_id}:{timestamp_ms}")
        self._journal.update_order(order)
        pending = dict(self._state.get("pending_orders", {}))
        pending.pop(order_id, None)
        self._state["pending_orders"] = pending
        self._state.setdefault("order_terminal_reasons", {})[order_id] = reason
        self._journal.save_runtime_state(self._state)

    def halt(self, reason: str) -> None:
        if not reason.strip():
            raise PaperRuntimeError("halt reason must be non-empty")
        self._persist_halt(reason)

    def acknowledge_recovery(self, acknowledgement: str) -> None:
        if not isinstance(acknowledgement, str) or not acknowledgement.strip():
            raise PaperRuntimeError("explicit recovery acknowledgement is required")
        latest = self._state.get("market_data_age_ms")
        if latest is not None and latest > self._risk.config.max_data_age_ms:
            raise PaperRuntimeError("recovery refused while latest market data is stale")
        if self._risk.kill_switch_active:
            raise PaperRuntimeError("recovery refused while the manual kill switch is active")
        if self._risk.config.kill_switch_file and Path(self._risk.config.kill_switch_file).exists():
            raise PaperRuntimeError("recovery refused while the kill-switch file exists")
        if float(self._state.get("daily_loss_fraction", 0.0)) >= self._risk.config.max_daily_loss_fraction:
            raise PaperRuntimeError("recovery refused while daily loss remains above its limit")
        if float(self._state.get("drawdown_fraction", 0.0)) >= self._risk.config.max_drawdown_fraction:
            raise PaperRuntimeError("recovery refused while drawdown remains above its limit")
        self._portfolio = self._journal.load_portfolio()
        self._journal.load_runtime_state()
        self._journal_failed = False
        self._state["halted"] = False
        self._state["halt_reason"] = ""
        self._state["recovery_acknowledgement_sha256"] = hashlib.sha256(acknowledgement.encode()).hexdigest()
        self._risk.halted = False
        self._risk.halt_reason = ""
        self._risk.consecutive_rejections = 0
        self._state["consecutive_failures"] = 0
        self._state["last_risk_verdict"] = RiskVerdict.ALLOW.value
        self._state["last_risk_reason_codes"] = []
        self._journal.save_runtime_state(self._state)
        self._remove_halt_marker()
        self._replay_incomplete_events()

    @property
    def is_halted(self) -> bool:
        """Whether a durable or in-memory runtime halt is active."""
        return bool(self._state.get("halted"))

    @property
    def halt_reason(self) -> str:
        """Return the persisted halt reason or an empty string when active."""
        return str(self._state.get("halt_reason", ""))

    @property
    def feed_receipt_binding(self) -> dict[str, str]:
        """Identity fields a public feed receipt must bind to this frozen runtime."""
        return {
            "candidate_id": self._candidate_id,
            "experiment_id": str(self._candidate.get("experiment_id", "")),
            "freeze_hash": str(self._candidate.get("freeze_hash", "")),
        }

    def metrics(self, book: OrderBookSnapshot) -> dict[str, Any]:
        mid = Decimal(str(book.mid_price))
        quantity = self._portfolio.base_quantity
        equity = self._portfolio.cash_krw + quantity * mid
        cost_basis = self._portfolio.cost_basis_krw
        return {
            "market_data_age_ms": int(self._state.get("market_data_age_ms", 0)),
            "strategy_state": self._state.get("strategy_state", "WARMUP"),
            "signal_count": int(self._state.get("signal_count", 0)),
            "order_count": int(self._state.get("order_count", 0)),
            "fill_count": int(self._state.get("fill_count", 0)),
            "rejections": int(self._state.get("rejections", 0)),
            "positions": {self._market: str(quantity)},
            "cash_krw": str(self._portfolio.cash_krw),
            "reserved_cash_krw": str(self._reserved_cash()),
            "average_entry_price": str(cost_basis / quantity if quantity else Decimal("0")),
            "equity_krw": str(equity),
            "realized_pnl_krw": str(self._portfolio.realized_pnl_krw),
            "unrealized_pnl_krw": str(quantity * mid - cost_basis),
            "fees_krw": str(self._portfolio.total_fees_paid_krw),
            "slippage_cost_krw": str(Decimal(str(self._state.get("slippage_cost_krw", "0")))),
            "turnover_krw": str(Decimal(str(self._state.get("turnover_krw", "0")))),
            "drawdown_fraction": float(self._state.get("drawdown_fraction", 0.0)),
            "risk_state": "HALTED" if self._state.get("halted") else "READY",
            "risk_verdict": self._state.get("last_risk_verdict", "ALLOW"),
            "risk_reason_codes": list(self._state.get("last_risk_reason_codes", [])),
            "halt_reason": self._state.get("halt_reason", ""),
            "journal_state": "FAILED" if self._journal_failed else "HEALTHY",
            "restart_count": int(self._state.get("restart_count", 0)),
            "fill_model": "CONSERVATIVE_VISIBLE_BOOK_TAKER",
            "order_types_supported": ["MARKET_TAKER"],
            "maker_orders": "UNSUPPORTED",
            "partial_fill_probability": "UNSUPPORTED",
            "depth_partial_policy": "ACCEPT" if self._accept_depth_partials else "REJECT",
            "latency_assumption_ms": self._scenario.latency_ms,
            "taker_fee_bps": self._scenario.taker_fee_bps,
            "slippage_bps": self._scenario.slippage_bps,
        }

    def _create_target_order(self, target_weight: float, event: NormalizedPaperEvent) -> None:
        pending = self._state.get("pending_orders", {})
        deterministic_order_id = _order_id(self._candidate_id, event.event_id)
        if deterministic_order_id in pending:
            self._restore_intent_for_replayed_event(deterministic_order_id, pending[deterministic_order_id])
            return
        if pending:
            return
        existing = self._journal.load_order_if_exists(deterministic_order_id)
        if existing is not None:
            metadata = self._journal.load_order_metadata(deterministic_order_id)
            if metadata is None or metadata.get("event_id") != event.event_id:
                raise PaperJournalIntegrityError("persisted intent is missing its matching recovery metadata")
            self._restore_intent_for_replayed_event(deterministic_order_id, metadata, existing)
            return
        signal_ms = _candle_timestamp_ms(event.candle) if event.candle else event.received_at_ms
        signal_book = next(
            (book for book in reversed(event.orderbooks) if _book_timestamp_ms(book) <= signal_ms),
            None,
        )
        if signal_book is None:
            self._persist_halt("MISSING_DECISION_ORDERBOOK")
            raise PaperRuntimeError("a point-in-time order book at or before the candle close is required")
        mid = Decimal(str(signal_book.mid_price))
        quantity = self._portfolio.base_quantity
        equity = self._portfolio.cash_krw + quantity * mid
        desired_notional = equity * Decimal(str(target_weight))
        delta = desired_notional - quantity * mid
        minimum = Decimal(str(self._scenario.minimum_order_notional))
        if abs(delta) < minimum:
            return
        side = "BUY" if delta > 0 else "SELL"
        fee_rate = Decimal(str(self._scenario.taker_fee_bps / 10_000.0))
        if side == "BUY":
            amount = min(delta, max(Decimal("0"), self._portfolio.cash_krw - self._reserved_cash()) / (1 + fee_rate))
            amount = _floor_decimal(amount, Decimal("1"))
            if amount < minimum:
                return
            order = PaperOrder(
                order_id=_order_id(self._candidate_id, event.event_id),
                idempotency_key=f"intent:{self._candidate_id}:{event.event_id}",
                market=self._market,
                side=side,
                requested_amount_krw=amount,
            )
            requested_notional = float(amount)
        else:
            requested_quantity = min(quantity, abs(delta) / mid)
            lot = Decimal(str(self._scenario.lot_size))
            requested_quantity = _floor_decimal(requested_quantity, lot)
            if requested_quantity <= 0:
                return
            order = PaperOrder(
                order_id=_order_id(self._candidate_id, event.event_id),
                idempotency_key=f"intent:{self._candidate_id}:{event.event_id}",
                market=self._market,
                side=side,
                requested_quantity_btc=requested_quantity,
            )
            requested_notional = float(requested_quantity * mid)

        timestamp = event.received_at_ms
        verdict, reasons, _audit = self._risk.evaluate_preflight(
            order_id=order.order_id,
            side=side,
            requested_notional_krw=requested_notional,
            current_equity_krw=float(equity),
            current_position_notional_krw=float(quantity * mid),
            daily_loss_fraction=float(self._state.get("daily_loss_fraction", 0.0)),
            orderbook=signal_book,
            current_time_ms=signal_ms,
            current_drawdown_fraction=float(self._state.get("drawdown_fraction", 0.0)),
        )
        self._state["last_risk_verdict"] = verdict.value
        self._state["last_risk_reason_codes"] = list(_audit.reason_codes)
        if self._state.get("halted") or self._risk.halted:
            verdict = RiskVerdict.HALT
            reasons = tuple(reasons) + (str(self._state.get("halt_reason") or self._risk.halt_reason or "PERSISTED_HALT"),)
        order_state = {
            "event_id": event.event_id,
            "signal_timestamp_ms": signal_ms,
            "signal_book": _book_dict(signal_book),
            "risk_verdict": verdict.value,
            "risk_reasons": list(reasons),
        }
        if verdict == RiskVerdict.ALLOW:
            order.transition_to(OrderStatus.RISK_APPROVED, timestamp)
            order.transition_to(OrderStatus.ACCEPTED, timestamp)
            order.transition_to(OrderStatus.SUBMITTED, timestamp)
            self._state["order_count"] = int(self._state.get("order_count", 0)) + 1
            self._journal.register_order(order, self._portfolio, runtime_metadata=order_state)
            current_pending = dict(self._state.get("pending_orders", {}))
            current_pending[order.order_id] = order_state
            self._state["pending_orders"] = current_pending
        else:
            self._risk.record_execution_outcome(False)
            self._state["rejections"] = int(self._state.get("rejections", 0)) + 1
            self._state["consecutive_failures"] = self._risk.consecutive_rejections
            if verdict == RiskVerdict.HALT:
                self._persist_halt(";".join(reasons) or "RISK_ENGINE_HALT")
            self._journal.register_order(order, self._portfolio, runtime_metadata=order_state)
            result = _rejected_result(order, signal_ms, self._scenario.taker_fee_bps / 10_000.0, ";".join(reasons) or verdict.value)
            self._journal.apply_execution_result(
                order, self._portfolio, result, timestamp,
                idempotency_key=f"risk:{order.idempotency_key}",
            )

    def _restore_intent_for_replayed_event(
        self,
        order_id: str,
        metadata: Mapping[str, Any],
        order: PaperOrder | None = None,
    ) -> None:
        """Complete the in-memory view of an intent whose event commit was interrupted."""
        recovered = order or self._journal.load_order(order_id)
        verdict = metadata.get("risk_verdict")
        if verdict == RiskVerdict.ALLOW.value:
            self._state["order_count"] = int(self._state.get("order_count", 0)) + 1
            if recovered.status not in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
                pending = dict(self._state.get("pending_orders", {}))
                pending[order_id] = dict(metadata)
                self._state["pending_orders"] = pending
            else:
                event_id = str(metadata["event_id"])
                prior = self._journal.get_execution_event(f"fill:{order_id}:{event_id}")
                if prior is not None:
                    prior_result = prior.get("result")
                    if not isinstance(prior_result, dict):
                        raise PaperJournalIntegrityError("replayed terminal order has invalid execution evidence")
                    fills = prior_result.get("fills", [])
                    if not isinstance(fills, list):
                        raise PaperJournalIntegrityError("replayed terminal order fill list is invalid")
                    if prior_result.get("status") == "REJECTED":
                        self._risk.record_execution_outcome(False)
                        self._state["consecutive_failures"] = self._risk.consecutive_rejections
                        self._state["rejections"] = int(self._state.get("rejections", 0)) + 1
                    else:
                        self._risk.record_execution_outcome(True)
                        self._state["consecutive_failures"] = 0
                        self._state["fill_count"] = int(self._state.get("fill_count", 0)) + len(fills)
                        self._state["turnover_krw"] = str(
                            Decimal(str(self._state.get("turnover_krw", "0")))
                            + Decimal(str(prior_result.get("filled_amount_krw", 0)))
                        )
                        self._state["slippage_cost_krw"] = str(
                            Decimal(str(self._state.get("slippage_cost_krw", "0")))
                            + max(
                                Decimal("0"),
                                Decimal(str(prior_result.get("total_cost_krw", 0)))
                                - Decimal(str(prior_result.get("fee_paid_krw", 0))),
                            )
                        )
            return
        if verdict not in {RiskVerdict.REJECT.value, RiskVerdict.HALT.value}:
            raise PaperJournalIntegrityError("persisted paper intent has an invalid risk verdict")
        self._state["rejections"] = int(self._state.get("rejections", 0)) + 1
        self._risk.record_execution_outcome(False)
        self._state["consecutive_failures"] = self._risk.consecutive_rejections
        if verdict == RiskVerdict.HALT.value:
            self._persist_halt(";".join(str(item) for item in metadata.get("risk_reasons", [])) or "RISK_ENGINE_HALT")
        if recovered.status == OrderStatus.CREATED:
            signal_ms = int(metadata["signal_timestamp_ms"])
            result = _rejected_result(
                recovered,
                signal_ms,
                self._scenario.taker_fee_bps / 10_000.0,
                ";".join(str(item) for item in metadata.get("risk_reasons", [])) or str(verdict),
            )
            self._journal.apply_execution_result(
                recovered,
                self._portfolio,
                result,
                signal_ms,
                idempotency_key=f"risk:{recovered.idempotency_key}",
            )

    def _advance_order(
        self, order_id: str, metadata: Mapping[str, Any], event: NormalizedPaperEvent
    ) -> dict[str, Any] | None:
        order = self._journal.load_order(order_id)
        if order.status in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
            pending = dict(self._state.get("pending_orders", {}))
            pending.pop(order_id, None)
            self._state["pending_orders"] = pending
            return None
        signal_ms = int(metadata["signal_timestamp_ms"])
        signal_book = _book_from_dict(metadata["signal_book"])
        last_execution_ms = self._journal.latest_execution_timestamp(order_id)
        future_books = [
            book for book in event.orderbooks
            if last_execution_ms is None or _book_timestamp_ms(book) > last_execution_ms
        ]
        books = sorted(
            {(_book_timestamp_ms(book), book.market, book): book for book in (signal_book, *future_books)}.values(),
            key=_book_timestamp_ms,
        )
        request_timestamp = signal_ms / 1000.0
        remaining_amount: Decimal | None = None
        remaining_quantity: Decimal | None = None
        if order.requested_amount_krw is not None:
            remaining_amount = max(Decimal("0"), order.requested_amount_krw - order.filled_amount_krw)
            request = MarketOrderRequest(
                timestamp=request_timestamp,
                side=order.side,
                requested_amount_krw=float(remaining_amount),
                fee_rate=self._scenario.taker_fee_bps / 10_000.0,
                latency_delay_ms=self._scenario.latency_ms,
                allow_partial=True,
                market=self._market,
            )
        else:
            total_quantity = order.requested_quantity_btc or Decimal("0")
            remaining_quantity = max(Decimal("0"), total_quantity - order.filled_quantity)
            request = MarketOrderRequest(
                timestamp=request_timestamp,
                side=order.side,
                requested_quantity_btc=float(remaining_quantity),
                fee_rate=self._scenario.taker_fee_bps / 10_000.0,
                latency_delay_ms=self._scenario.latency_ms,
                allow_partial=True,
                market=self._market,
            )
        if (remaining_amount is not None and remaining_amount <= 0) or (remaining_quantity is not None and remaining_quantity <= 0):
            return None

        fill_key = f"fill:{order_id}:{event.event_id}"
        prior_execution = self._journal.get_execution_event(fill_key)
        if prior_execution is not None:
            if prior_execution.get("order_id") != order_id or prior_execution.get("timestamp_ms") != event.received_at_ms:
                raise PaperJournalIntegrityError("persisted fill event does not match the replayed market event")
            prior_result = prior_execution.get("result")
            if not isinstance(prior_result, dict):
                raise PaperJournalIntegrityError("persisted fill event has no execution result")
            fills = prior_result.get("fills", [])
            if not isinstance(fills, list):
                raise PaperJournalIntegrityError("persisted fill list is invalid")
            if prior_result.get("status") == "REJECTED":
                self._risk.record_execution_outcome(False)
                self._state["consecutive_failures"] = self._risk.consecutive_rejections
                self._state["rejections"] = int(self._state.get("rejections", 0)) + 1
                pending = dict(self._state.get("pending_orders", {}))
                pending.pop(order_id, None)
                self._state["pending_orders"] = pending
            else:
                self._risk.record_execution_outcome(True)
                self._state["consecutive_failures"] = 0
                self._state["fill_count"] = int(self._state.get("fill_count", 0)) + len(fills)
                self._state["turnover_krw"] = str(
                    Decimal(str(self._state.get("turnover_krw", "0")))
                    + Decimal(str(prior_result.get("filled_amount_krw", 0)))
                )
                self._state["slippage_cost_krw"] = str(
                    Decimal(str(self._state.get("slippage_cost_krw", "0")))
                    + max(
                        Decimal("0"),
                        Decimal(str(prior_result.get("total_cost_krw", 0)))
                        - Decimal(str(prior_result.get("fee_paid_krw", 0))),
                    )
                )
                if self._journal.load_order(order_id).status in {
                    OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED
                }:
                    pending = dict(self._state.get("pending_orders", {}))
                    pending.pop(order_id, None)
                    self._state["pending_orders"] = pending
            return {
                "order_id": order_id,
                "status": self._journal.load_order(order_id).status.value,
                "applied": False,
                "recovered_execution": True,
                "fill_count": len(fills),
            }

        raw_result = DeterministicTakerSimulator.execute_with_latency(
            request, books, max_book_age_ms=self._risk.config.max_data_age_ms, fail_closed=True
        )
        if raw_result.rejection_reason == "INSUFFICIENT_FUTURE_DATA":
            return None
        result = self._constrain_execution(raw_result)
        if order.requested_amount_krw is not None or order.requested_quantity_btc is not None:
            original_request = MarketOrderRequest(
                timestamp=request_timestamp,
                side=order.side,
                requested_amount_krw=float(order.requested_amount_krw) if order.requested_amount_krw is not None else None,
                requested_quantity_btc=float(order.requested_quantity_btc) if order.requested_quantity_btc is not None else None,
                fee_rate=self._scenario.taker_fee_bps / 10_000.0,
                latency_delay_ms=self._scenario.latency_ms,
                allow_partial=True,
                market=self._market,
            )
            result = replace(result, request=original_request)

        if result.is_rejected:
            order_result = self._journal.apply_execution_result(
                order, self._portfolio, result, event.received_at_ms,
                idempotency_key=fill_key,
            )
            self._risk.record_execution_outcome(False)
            self._state["consecutive_failures"] = self._risk.consecutive_rejections
            self._state["rejections"] = int(self._state.get("rejections", 0)) + 1
            pending = dict(self._state.get("pending_orders", {}))
            pending.pop(order_id, None)
            self._state["pending_orders"] = pending
            return {"order_id": order_id, "status": order.status.value, "applied": order_result, "reason": result.rejection_reason}

        applied = self._journal.apply_execution_result(
            order, self._portfolio, result, event.received_at_ms,
            idempotency_key=fill_key,
        )
        self._risk.record_execution_outcome(True)
        self._state["consecutive_failures"] = 0
        self._state["fill_count"] = int(self._state.get("fill_count", 0)) + len(result.fills)
        self._state["turnover_krw"] = str(
            Decimal(str(self._state.get("turnover_krw", "0"))) + Decimal(str(result.filled_amount_krw))
        )
        self._state["slippage_cost_krw"] = str(
            Decimal(str(self._state.get("slippage_cost_krw", "0")))
            + max(Decimal("0"), Decimal(str(result.total_cost_krw - result.fee_paid_krw)))
        )
        terminal = order.status in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
        if terminal:
            pending = dict(self._state.get("pending_orders", {}))
            pending.pop(order_id, None)
            self._state["pending_orders"] = pending
        return {
            "order_id": order_id,
            "status": order.status.value,
            "applied": applied,
            "filled_quantity": str(order.filled_quantity),
            "filled_amount_krw": str(order.filled_amount_krw),
            "fees_krw": str(order.fee_paid_krw),
            "slippage_cost_krw": str(max(Decimal("0"), Decimal(str(result.total_cost_krw - result.fee_paid_krw)))),
            "fill_count": len(result.fills),
        }

    def _constrain_execution(self, result: ExecutionResult) -> ExecutionResult:
        if result.is_rejected:
            return result
        side = result.side.upper()
        tick = Decimal(str(self._scenario.tick_size))
        lot = Decimal(str(self._scenario.lot_size))
        rounded: list[ExecutionFill] = []
        scenario_slippage_cost = Decimal("0")
        remaining_notional = (
            Decimal(str(result.request.requested_amount_krw))
            if result.request.requested_amount_krw is not None
            else None
        )
        for fill in result.fills:
            price_decimal = Decimal(str(fill.price))
            if price_decimal / tick != (price_decimal / tick).to_integral_value():
                return _rejected_result_from(result, "BOOK_PRICE_OFF_TICK")
            raw_slipped_price = Decimal(str(spot_execution_price(self._scenario, fill.price, side)))
            price_ticks = raw_slipped_price / tick
            tick_count = (
                price_ticks.to_integral_value(rounding=ROUND_CEILING)
                if side == "BUY"
                else price_ticks.to_integral_value(rounding=ROUND_DOWN)
            )
            slipped_price = tick_count * tick
            quantity = _floor_decimal(Decimal(str(fill.size)), lot)
            if remaining_notional is not None:
                affordable = _floor_decimal(max(Decimal("0"), remaining_notional) / slipped_price, lot)
                quantity = min(quantity, affordable)
            if quantity <= 0:
                continue
            notional = slipped_price * quantity
            scenario_slippage_cost += abs(slipped_price - price_decimal) * quantity
            rounded.append(ExecutionFill(
                level_index=fill.level_index,
                price=float(slipped_price),
                size=float(quantity),
                notional_krw=float(notional),
            ))
            if remaining_notional is not None:
                remaining_notional -= notional
        total_quantity = sum((Decimal(str(item.size)) for item in rounded), Decimal("0"))
        total_notional = sum((Decimal(str(item.notional_krw)) for item in rounded), Decimal("0"))
        if not rounded or total_quantity <= 0 or total_notional < Decimal(str(self._scenario.minimum_order_notional)):
            return _rejected_result_from(result, "NO_TRADEABLE_LOT_OR_MINIMUM_NOTIONAL")
        raw_requested_amount = result.request.requested_amount_krw
        raw_requested_quantity = result.request.requested_quantity_btc
        if raw_requested_amount is not None:
            remainder = max(Decimal("0"), Decimal(str(raw_requested_amount)) - total_notional)
            min_next_lot = Decimal(str(max(item.price for item in rounded))) * lot
            partial = remainder >= min_next_lot
            unfilled_amount = float(remainder)
            unfilled_quantity = float(remainder / Decimal(str(result.vwap_price))) if remainder else 0.0
        else:
            requested = Decimal(str(raw_requested_quantity or 0))
            remainder_quantity = max(Decimal("0"), requested - total_quantity)
            partial = remainder_quantity >= lot
            unfilled_quantity = float(remainder_quantity)
            unfilled_amount = float(remainder_quantity * Decimal(str(result.vwap_price)))
        if partial and not self._accept_depth_partials:
            return _rejected_result_from(result, "PARTIAL_DEPTH_FILL_REJECTED_BY_POLICY")
        status = "PARTIALLY_FILLED" if partial else "FILLED"
        fees = total_notional * Decimal(str(result.fee_rate))
        vwap = total_notional / total_quantity
        mid_order = Decimal(str(result.mid_price_at_order))
        top_fill = Decimal(str(result.top_of_book_at_fill))
        directional_mid_slippage = (
            max(Decimal("0"), vwap - mid_order)
            if side == "BUY"
            else max(Decimal("0"), mid_order - vwap)
        )
        directional_top_slippage = (
            max(Decimal("0"), vwap - top_fill)
            if side == "BUY"
            else max(Decimal("0"), top_fill - vwap)
        )
        microstructure_slippage = sum(
            Decimal(str(value))
            for value in (
                result.half_spread_cost_krw,
                result.depth_slippage_cost_krw,
                result.latency_slippage_cost_krw,
            )
        )
        return replace(
            result,
            status=status,
            filled_quantity=float(total_quantity),
            filled_amount_krw=float(total_notional),
            unfilled_quantity=unfilled_quantity,
            unfilled_amount_krw=unfilled_amount,
            vwap_price=float(vwap),
            fee_paid_krw=float(fees),
            slippage_vs_mid_bps=float(directional_mid_slippage / mid_order * Decimal("10000")) if mid_order > 0 else 0.0,
            slippage_vs_top_bps=float(directional_top_slippage / top_fill * Decimal("10000")) if top_fill > 0 else 0.0,
            fills=tuple(rounded),
            depth_slippage_cost_krw=result.depth_slippage_cost_krw + float(scenario_slippage_cost),
            total_cost_krw=float(microstructure_slippage + scenario_slippage_cost + fees),
        )

    def _validate_frozen_strategy(self, metrics_bytes: bytes, warmup_candles: Sequence[Candle]) -> None:
        metrics = json.loads(metrics_bytes)
        if not isinstance(metrics, dict) or metrics.get("strategy_id") != self._candidate.get("strategy_id"):
            raise PaperRuntimeError("experiment metrics do not identify the frozen strategy")
        if metrics.get("code_revision") != self._candidate.get("code_revision"):
            raise PaperRuntimeError("experiment metrics code revision differs from the freeze")
        if not warmup_candles:
            raise PaperRuntimeError("frozen strategy requires committed development warmup candles")
        if any(warmup_candles[i].timestamp <= warmup_candles[i - 1].timestamp for i in range(1, len(warmup_candles))):
            raise PaperRuntimeError("warmup candles must be strictly chronological")
        if any(candle.market != self._market for candle in warmup_candles):
            raise PaperRuntimeError("warmup candle market differs from runtime market")
        expected = _frozen_parameter_hash(metrics)
        if _hash_json(dict(self._strategy.parameters())) != expected:
            raise PaperRuntimeError("strategy parameter manifest differs from frozen experiment folds")

    def _validate_event(self, event: NormalizedPaperEvent) -> None:
        if not isinstance(event.event_id, str) or not event.event_id.strip():
            raise PaperRuntimeError("normalized paper event requires a stable event_id")
        if isinstance(event.received_at_ms, bool) or not isinstance(event.received_at_ms, int) or event.received_at_ms < 0:
            raise PaperRuntimeError("normalized paper event timestamp is invalid")
        if not event.orderbooks:
            raise PaperRuntimeError("normalized paper event must include public order-book snapshots")
        books = event.orderbooks
        if any(book.market != self._market for book in books):
            raise PaperRuntimeError("order-book market differs from paper runtime market")
        times = [_book_timestamp_ms(book) for book in books]
        if any(times[index] <= times[index - 1] for index in range(1, len(times))):
            raise PaperRuntimeError("order-book snapshots must be strictly chronological")
        if times[-1] > event.received_at_ms:
            raise PaperRuntimeError("order-book snapshot is from the future")
        if event.candle is not None and event.candle.market != self._market:
            raise PaperRuntimeError("candle market differs from paper runtime market")
        if event.candle is not None and _candle_timestamp_ms(event.candle) > event.received_at_ms:
            raise PaperRuntimeError("candle timestamp is from the future")

    def _append_candle(self, candle: Candle) -> None:
        rows = list(self._state.get("candles", []))
        timestamp = candle.timestamp.isoformat()
        if rows and timestamp <= rows[-1]["timestamp"]:
            raise PaperRuntimeError("strategy candle history must be strictly chronological")
        rows.append(_candle_dict(candle))
        self._state["candles"] = rows[-2_000:]

    def _candles(self) -> tuple[Candle, ...]:
        return tuple(_candle_from_dict(item) for item in self._state.get("candles", []))

    def _pending_from_journal(self) -> dict[str, Any]:
        existing = dict(self._state.get("pending_orders", {}))
        for order in self._journal.load_open_orders():
            if order.order_id not in existing:
                metadata = self._journal.load_order_metadata(order.order_id)
                if metadata is None:
                    raise PaperJournalIntegrityError("open paper order has no recovery metadata")
                if metadata.get("risk_verdict") == RiskVerdict.ALLOW.value:
                    existing[order.order_id] = metadata
        return existing

    def _replay_incomplete_events(self) -> None:
        for payload in self._journal.load_uncommitted_runtime_events():
            event = _event_from_dict(payload)
            self.process_event(event)

    def _reserved_cash(self) -> Decimal:
        reserved = Decimal("0")
        for order in self._journal.load_open_orders():
            if order.side.upper() == "BUY" and order.requested_amount_krw is not None:
                remaining = max(Decimal("0"), order.requested_amount_krw - order.filled_amount_krw)
                reserved += remaining * (1 + Decimal(str(self._scenario.taker_fee_bps / 10_000.0)))
        return reserved

    def _update_account_metrics(self, book: OrderBookSnapshot) -> None:
        mid = Decimal(str(book.mid_price))
        equity = self._portfolio.cash_krw + self._portfolio.base_quantity * mid
        previous_high = Decimal(str(self._state.get("high_water_equity_krw", str(equity))))
        high = max(previous_high, equity)
        self._state["high_water_equity_krw"] = str(high)
        self._state["drawdown_fraction"] = float((high - equity) / high) if high > 0 else 1.0
        event_day = datetime.fromtimestamp(_book_timestamp_ms(book) / 1000.0, tz=UTC).date().isoformat()
        day_start = Decimal(str(self._state.get("day_start_equity_krw", str(equity))))
        if self._state.get("day_key") != event_day:
            day_start = equity
            self._state["day_key"] = event_day
            self._state["day_start_equity_krw"] = str(equity)
        self._state["daily_loss_fraction"] = float(max(Decimal("0"), (day_start - equity) / day_start)) if day_start > 0 else 1.0

    def _persist_halt(self, reason: str) -> None:
        self._state["halted"] = True
        self._state["halt_reason"] = reason
        self._state["last_risk_verdict"] = RiskVerdict.HALT.value
        self._state["last_risk_reason_codes"] = [_runtime_reason_code(reason)]
        self._risk.halted = True
        self._risk.halt_reason = reason
        marker_failed: Exception | None = None
        try:
            self._write_halt_marker(reason)
        except Exception as exc:
            marker_failed = exc
        journal_failed: Exception | None = None
        try:
            self._journal.save_runtime_state(self._state)
        except Exception as exc:
            self._journal_failed = True
            self._risk.halted = True
            journal_failed = exc
        if marker_failed is not None and journal_failed is not None:
            raise PaperJournalIntegrityError(
                "both local halt marker and journal state failed to persist; runtime must remain stopped"
            ) from journal_failed

    def _read_halt_marker(self) -> str | None:
        if not self._halt_marker_path.exists():
            return None
        if self._halt_marker_path.is_symlink() or not self._halt_marker_path.is_file():
            raise PaperJournalIntegrityError("paper halt marker is not a regular local file")
        try:
            payload = json.loads(self._halt_marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PaperJournalIntegrityError("paper halt marker is unreadable; fail closed") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "reason", "marker_sha256"}
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("reason"), str)
            or not payload["reason"].strip()
        ):
            raise PaperJournalIntegrityError("paper halt marker schema is invalid; fail closed")
        unsigned = {key: value for key, value in payload.items() if key != "marker_sha256"}
        if payload.get("marker_sha256") != _hash_json(unsigned):
            raise PaperJournalIntegrityError("paper halt marker checksum mismatch; fail closed")
        return payload["reason"]

    def _write_halt_marker(self, reason: str) -> None:
        unsigned = {"schema_version": 1, "reason": reason}
        payload = {**unsigned, "marker_sha256": _hash_json(unsigned)}
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        self._halt_marker_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self._halt_marker_path.name}.",
            dir=self._halt_marker_path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._halt_marker_path)
            directory_fd = os.open(self._halt_marker_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _remove_halt_marker(self) -> None:
        if self._halt_marker_path.is_symlink():
            raise PaperJournalIntegrityError("refusing to remove a symlink paper halt marker")
        try:
            self._halt_marker_path.unlink(missing_ok=True)
        except OSError as exc:
            raise PaperJournalIntegrityError("recovery acknowledgement could not clear durable halt marker") from exc

    def _new_state(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "candidate_id": self._candidate_id,
            "market": self._market,
            "candles": [],
            "pending_orders": {},
            "last_event_timestamp_ms": None,
            "target_weight": None,
            "strategy_state": "WARMUP",
            "signal_count": 0,
            "order_count": 0,
            "fill_count": 0,
            "rejections": 0,
            "consecutive_failures": 0,
            "turnover_krw": "0",
            "slippage_cost_krw": "0",
            "drawdown_fraction": 0.0,
            "daily_loss_fraction": 0.0,
            "halted": False,
            "halt_reason": "",
            "last_risk_verdict": RiskVerdict.ALLOW.value,
            "last_risk_reason_codes": [],
            "restart_count": 0,
        }


def _rejected_result(order: PaperOrder, timestamp_ms: int, fee_rate: float, reason: str) -> ExecutionResult:
    request = MarketOrderRequest(
        timestamp=timestamp_ms / 1000.0,
        side=order.side,
        requested_amount_krw=float(order.requested_amount_krw) if order.requested_amount_krw is not None else None,
        requested_quantity_btc=float(order.requested_quantity_btc) if order.requested_quantity_btc is not None else None,
        fee_rate=fee_rate,
        latency_delay_ms=0.0,
        allow_partial=True,
        market=order.market,
    )
    return ExecutionResult(
        request=request,
        order_timestamp=timestamp_ms / 1000.0,
        fill_timestamp=timestamp_ms / 1000.0,
        status="REJECTED",
        side=order.side,
        filled_quantity=0.0,
        filled_amount_krw=0.0,
        unfilled_quantity=float(order.requested_quantity_btc or 0),
        unfilled_amount_krw=float(order.requested_amount_krw or 0),
        vwap_price=0.0,
        mid_price_at_order=0.0,
        top_of_book_at_order=0.0,
        mid_price_at_fill=0.0,
        top_of_book_at_fill=0.0,
        fee_rate=fee_rate,
        fee_paid_krw=0.0,
        slippage_vs_mid_bps=0.0,
        slippage_vs_top_bps=0.0,
        adverse_selection_bps=0.0,
        latency_delay_ms=0.0,
        rejection_reason=reason,
    )


def _rejected_result_from(result: ExecutionResult, reason: str) -> ExecutionResult:
    return replace(
        result,
        status="REJECTED",
        filled_quantity=0.0,
        filled_amount_krw=0.0,
        fee_paid_krw=0.0,
        unfilled_quantity=float(result.request.requested_quantity_btc or 0),
        unfilled_amount_krw=float(result.request.requested_amount_krw or 0),
        vwap_price=0.0,
        fills=(),
        rejection_reason=reason,
        total_cost_krw=0.0,
    )


def _frozen_parameter_hash(metrics: Mapping[str, Any]) -> str:
    folds = metrics.get("folds")
    if not isinstance(folds, list) or not folds:
        raise PaperRuntimeError("frozen experiment has no walk-forward parameter evidence")
    hashes = {row.get("frozen_parameter_sha256") for row in folds if isinstance(row, dict)}
    if len(hashes) != 1 or not isinstance(next(iter(hashes)), str):
        raise PaperRuntimeError("frozen strategy parameter hash is inconsistent across folds")
    return str(next(iter(hashes)))


def _validate_paper_cost_scenario(
    candidate: Mapping[str, Any], selected: SpotCostScenario
) -> None:
    raw_scenarios = candidate.get("cost_scenarios")
    if not isinstance(raw_scenarios, list):
        raise PaperRuntimeError("frozen candidate does not bind its cost sensitivity scenarios")
    extreme = next(
        (row for row in raw_scenarios if isinstance(row, dict) and row.get("name") == "extreme"),
        None,
    )
    if extreme is None:
        raise PaperRuntimeError("frozen candidate has no extreme cost tier")
    try:
        frozen = SpotCostScenario.from_dict(extreme)
    except (TypeError, ValueError) as exc:
        raise PaperRuntimeError("frozen extreme cost scenario is invalid") from exc
    if (
        selected.maker_fee_bps < frozen.maker_fee_bps
        or selected.taker_fee_bps < frozen.taker_fee_bps
        or selected.slippage_bps < frozen.slippage_bps
        or selected.latency_ms < frozen.latency_ms
        or selected.minimum_order_notional < frozen.minimum_order_notional
        or selected.tick_size != frozen.tick_size
        or selected.lot_size != frozen.lot_size
    ):
        raise PaperRuntimeError("paper cost scenario is more optimistic or differently rounded than the frozen extreme tier")


def _current_strategy_source_hash(strategy_id: str) -> str:
    if strategy_id not in governed_candidate_strategy_ids():
        raise PaperRuntimeError("strategy source is outside the runtime allowlist")
    package_root = Path(__file__).parent
    modules = strategy_source_modules(strategy_id)
    rows = []
    for module in modules:
        path = package_root / module
        if path.is_symlink() or not path.is_file():
            raise PaperRuntimeError(f"strategy source is missing or unsafe: {module}")
        rows.append({
            "path": f"src/bithumb_coin_trader/{module}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return _hash_json(rows)


def _event_dict(event: NormalizedPaperEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "received_at_ms": event.received_at_ms,
        "candle": _candle_dict(event.candle) if event.candle is not None else None,
        "orderbooks": [_book_dict(book) for book in event.orderbooks],
        "source_observation_sha256": list(event.source_observation_sha256),
    }


def _event_from_dict(payload: Mapping[str, Any]) -> NormalizedPaperEvent:
    raw_candle = payload.get("candle")
    raw_books = payload.get("orderbooks")
    if raw_candle is not None and not isinstance(raw_candle, dict):
        raise PaperJournalIntegrityError("persisted normalized event candle is invalid")
    if not isinstance(raw_books, list) or not raw_books or any(not isinstance(book, dict) for book in raw_books):
        raise PaperJournalIntegrityError("persisted normalized event order books are invalid")
    return NormalizedPaperEvent(
        event_id=str(payload.get("event_id", "")),
        received_at_ms=int(payload.get("received_at_ms", -1)),
        candle=_candle_from_dict(raw_candle) if raw_candle is not None else None,
        orderbooks=tuple(_book_from_dict(book) for book in raw_books),
    )


def _candle_dict(candle: Candle) -> dict[str, Any]:
    return {
        "timestamp": candle.timestamp.isoformat(), "open": candle.open, "high": candle.high,
        "low": candle.low, "close": candle.close, "volume": candle.volume, "market": candle.market,
    }


def _candle_from_dict(row: Mapping[str, Any]) -> Candle:
    timestamp = datetime.fromisoformat(str(row["timestamp"]))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return Candle(
        timestamp=timestamp, open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
        close=float(row["close"]), volume=float(row["volume"]), market=str(row["market"]),
    )


def _book_dict(book: OrderBookSnapshot) -> dict[str, Any]:
    return {
        "timestamp": _book_timestamp_ms(book) / 1000.0,
        "market": book.market,
        "bids": [list(row) for row in book.bids],
        "asks": [list(row) for row in book.asks],
    }


def _book_from_dict(row: Mapping[str, Any]) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        timestamp=float(row["timestamp"]),
        market=str(row["market"]),
        bids=tuple((float(price), float(size)) for price, size in row["bids"]),
        asks=tuple((float(price), float(size)) for price, size in row["asks"]),
        validate=True,
    )


def _book_timestamp_ms(book: OrderBookSnapshot) -> int:
    value = book.timestamp
    seconds = value.timestamp() if isinstance(value, datetime) else float(value)
    return int(seconds * 1000)


def _candle_timestamp_ms(candle: Candle | None) -> int:
    if candle is None:
        raise PaperRuntimeError("strategy decision event has no candle")
    return int(candle.timestamp.timestamp() * 1000)


def _floor_decimal(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        raise PaperRuntimeError("execution increments must be positive")
    return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment


def _order_id(candidate_id: str, event_id: str) -> str:
    digest = hashlib.sha256(f"{candidate_id}:{event_id}".encode()).hexdigest()
    return f"paper_{digest}"


def _runtime_reason_code(reason: str) -> str:
    normalized = reason.upper()
    for code in (
        "PAPER_JOURNAL_FAILURE", "PAPER_ACCOUNTING_INVARIANT_FAILURE", "STALE_MARKET_DATA", "MARKET_DATA_STALE", "MAX_DRAWDOWN", "DAILY_LOSS_LIMIT",
        "MANUAL_KILL_SWITCH", "KILL_SWITCH_ACTIVE", "STRATEGY_FAILURE", "STRATEGY_STALE", "JOURNAL_UNHEALTHY", "INVALID_STRATEGY_OUTPUT",
        "OUT_OF_ORDER_MARKET_EVENT", "MISSING_DECISION_ORDERBOOK", "OPEN_ORDER_RECOVERY_METADATA_MISSING",
    ):
        if code in normalized:
            return code
    return "RUNTIME_HALTED"


def _load_json_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PaperRuntimeError("frozen experiment metrics are missing or a symlink")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperRuntimeError("frozen experiment metrics are unreadable") from exc
    if not isinstance(value, dict):
        raise PaperRuntimeError("frozen experiment metrics must be a JSON object")
    return value


def _hash_json(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()
