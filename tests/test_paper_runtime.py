from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bithumb_coin_trader.execution_simulator import OrderBookSnapshot
from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.paper_engine import OrderStatus
from bithumb_coin_trader.paper_journal import PaperEventJournal
from bithumb_coin_trader.paper_runtime import NormalizedPaperEvent, PaperRuntime
from bithumb_coin_trader.research_infra.candidate_freeze import freeze_candidate_experiment
from bithumb_coin_trader.research_infra.costs import SpotCostScenario
from bithumb_coin_trader.risk_engine import RiskEngine, RiskEngineConfig
from tests.research_infra.test_candidate_freeze import _candles, _prepare_selected_experiment


@pytest.fixture(scope="session")
def frozen_candidate(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, object], list[Candle]]:
    root = tmp_path_factory.mktemp("paper-runtime-candidate")
    research_root, registry_path, candidate_id, _attempt_dir = _prepare_selected_experiment(root)
    artifact = freeze_candidate_experiment(
        experiment_id=candidate_id,
        candidate_id=candidate_id,
        research_root=research_root,
        candidate_registry_path=registry_path,
        output_path=research_root / "frozen-candidates" / f"{candidate_id}.json",
    )
    return research_root, artifact, _candles()


def _scenario() -> SpotCostScenario:
    return SpotCostScenario(
        name="paper-conservative",
        maker_fee_bps=8.0,
        taker_fee_bps=16.0,
        slippage_bps=10.0,
        latency_ms=1_000.0,
        minimum_order_notional=1.0,
        tick_size=1.0,
        lot_size=0.00000001,
        partial_fill_probability=None,
        partial_fill_status="UNSUPPORTED",
    )


def _runtime(
    frozen_candidate: tuple[Path, dict[str, object], list[Candle]], path: Path
) -> PaperRuntime:
    research_root, artifact, warmup = frozen_candidate
    return PaperRuntime.from_frozen_artifact(
        candidate_artifact=artifact,
        research_root=research_root,
        warmup_candles=warmup,
        cost_scenario=_scenario(),
        accept_depth_partials=True,
        journal=PaperEventJournal(path),
        risk_engine=RiskEngine(RiskEngineConfig(
            max_order_notional_krw=10_000_000.0,
            max_portfolio_exposure_fraction=1.0,
            max_spread_bps=50.0,
            max_slippage_bps=30.0,
            max_data_age_ms=5_000.0,
        )),
        initial_cash_krw=Decimal("1000000"),
        market="KRW-BTC",
    )


def _event(warmup: list[Candle], event_id: str, *, depth: float = 1_000.0) -> NormalizedPaperEvent:
    previous = warmup[-1]
    timestamp = previous.timestamp + timedelta(days=1)
    close = previous.close * 1.01
    candle = Candle(
        timestamp=timestamp,
        open=previous.close,
        high=close * 1.001,
        low=previous.close * 0.999,
        close=close,
        volume=1_000.0,
        market="KRW-BTC",
    )
    price = float(round(close))
    signal_time = timestamp.timestamp()
    fill_time = signal_time + 1.0
    signal_book = OrderBookSnapshot(
        timestamp=signal_time,
        market="KRW-BTC",
        bids=((price - 10.0, depth),),
        asks=((price + 10.0, depth),),
    )
    fill_book = OrderBookSnapshot(
        timestamp=fill_time,
        market="KRW-BTC",
        bids=((price - 10.0, depth),),
        asks=((price + 10.0, depth),),
    )
    return NormalizedPaperEvent(
        event_id=event_id,
        received_at_ms=int((fill_time + 0.1) * 1000),
        candle=candle,
        orderbooks=(signal_book, fill_book),
    )


def test_runtime_uses_frozen_candidate_and_replays_duplicate_event_idempotently(
    frozen_candidate: tuple[Path, dict[str, object], list[Candle]], tmp_path: Path
) -> None:
    runtime = _runtime(frozen_candidate, tmp_path / "paper.sqlite3")
    event = _event(frozen_candidate[2], "market-1")

    first = runtime.process_event(event)
    before = runtime.metrics(event.orderbooks[-1])
    repeated = runtime.process_event(event)

    assert repeated == first
    assert runtime.metrics(event.orderbooks[-1]) == before
    assert Decimal(before["cash_krw"]) >= 0
    assert Decimal(before["positions"]["KRW-BTC"]) > 0
    assert Decimal(before["cash_krw"]) + Decimal(before["positions"]["KRW-BTC"]) * Decimal(str(event.orderbooks[-1].mid_price)) == Decimal(before["equity_krw"])
    assert Decimal(before["fees_krw"]) > 0
    assert before["fill_count"] >= 1


def test_partial_fill_then_fill_while_cancel_pending_is_accounted_once(
    frozen_candidate: tuple[Path, dict[str, object], list[Candle]], tmp_path: Path
) -> None:
    runtime = _runtime(frozen_candidate, tmp_path / "partial.sqlite3")
    warmup = frozen_candidate[2]
    first_event = _event(warmup, "partial-1", depth=30.0)
    first = runtime.process_event(first_event)
    order_id = first["orders"][0]["order_id"]
    assert first["orders"][0]["status"] == OrderStatus.PARTIALLY_FILLED.value

    runtime.cancel_order(order_id, first_event.received_at_ms + 1)
    later_time = (first_event.received_at_ms + 200) / 1000.0
    later_book = OrderBookSnapshot(
        timestamp=later_time,
        market="KRW-BTC",
        bids=first_event.orderbooks[-1].bids,
        asks=((first_event.orderbooks[-1].asks[0][0], 1_000.0),),
    )
    second_event = NormalizedPaperEvent(
        event_id="partial-2",
        received_at_ms=int((later_time + 0.1) * 1000),
        candle=None,
        orderbooks=(later_book,),
    )
    second = runtime.process_event(second_event)
    assert second["orders"][0]["status"] == OrderStatus.FILLED.value
    assert runtime.metrics(later_book)["positions"]["KRW-BTC"] != "0"


def test_paper_halt_survives_restart_until_explicit_recovery_acknowledgement(
    frozen_candidate: tuple[Path, dict[str, object], list[Candle]], tmp_path: Path
) -> None:
    db_path = tmp_path / "halt.sqlite3"
    runtime = _runtime(frozen_candidate, db_path)
    runtime.halt("MANUAL_OPERATOR_HALT")

    restarted = _runtime(frozen_candidate, db_path)
    assert restarted._state["halted"] is True
    event = _event(frozen_candidate[2], "after-restart")
    with pytest.raises(ValueError, match="explicit recovery acknowledgement"):
        restarted.acknowledge_recovery("   ")
    restarted.acknowledge_recovery("reviewed local journal and account state")
    result = restarted.process_event(event)
    assert result["metrics"]["risk_state"] == "READY"
    assert Decimal(result["metrics"]["positions"]["KRW-BTC"]) > 0
    assert not Path(f"{db_path}.halt.json").exists()


def test_durable_halt_marker_wins_over_a_nonhalted_journal_snapshot(
    frozen_candidate: tuple[Path, dict[str, object], list[Candle]], tmp_path: Path
) -> None:
    db_path = tmp_path / "halt-marker.sqlite3"
    runtime = _runtime(frozen_candidate, db_path)
    runtime.halt("MANUAL_OPERATOR_HALT")
    state = runtime._journal.load_runtime_state()
    assert state is not None
    state["halted"] = False
    state["halt_reason"] = ""
    runtime._journal.save_runtime_state(state)

    restarted = _runtime(frozen_candidate, db_path)
    assert restarted._state["halted"] is True
    assert restarted._state["halt_reason"] == "MANUAL_OPERATOR_HALT"


def test_corrupt_halt_marker_fails_closed(
    frozen_candidate: tuple[Path, dict[str, object], list[Candle]], tmp_path: Path
) -> None:
    db_path = tmp_path / "corrupt-halt-marker.sqlite3"
    runtime = _runtime(frozen_candidate, db_path)
    runtime.halt("MANUAL_OPERATOR_HALT")
    Path(f"{db_path}.halt.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="halt marker schema is invalid"):
        _runtime(frozen_candidate, db_path)


def test_stale_data_halts_without_emitting_an_order(
    frozen_candidate: tuple[Path, dict[str, object], list[Candle]], tmp_path: Path
) -> None:
    runtime = _runtime(frozen_candidate, tmp_path / "stale.sqlite3")
    event = _event(frozen_candidate[2], "stale-event")
    stale_event = NormalizedPaperEvent(
        event_id=event.event_id,
        received_at_ms=event.received_at_ms + 6_000,
        candle=event.candle,
        orderbooks=event.orderbooks,
    )

    result = runtime.process_event(stale_event)
    assert result["orders"] == []
    assert result["metrics"]["risk_state"] == "HALTED"
    assert "MARKET_DATA_STALE" in result["metrics"]["risk_reason_codes"]


def test_journal_failure_persists_halt_and_reports_failed_observability(
    frozen_candidate: tuple[Path, dict[str, object], list[Candle]], tmp_path: Path
) -> None:
    runtime = _runtime(frozen_candidate, tmp_path / "journal-failure.sqlite3")
    event = _event(frozen_candidate[2], "journal-failure-event")

    def fail_event_commit(*_args: object, **_kwargs: object) -> bool:
        raise OSError("injected local journal failure")

    runtime._journal.record_runtime_event = fail_event_commit  # type: ignore[method-assign]
    with pytest.raises(OSError, match="injected local journal failure"):
        runtime.process_event(event)

    metrics = runtime.metrics(event.orderbooks[-1])
    assert metrics["risk_state"] == "HALTED"
    assert metrics["journal_state"] == "FAILED"


@pytest.mark.parametrize(
    "crash_point",
    ["before_event_journal", "after_order_journal", "before_accounting", "after_accounting"],
)
def test_restart_recovers_crash_around_atomic_accounting_commit(
    frozen_candidate: tuple[Path, dict[str, object], list[Candle]],
    tmp_path: Path,
    crash_point: str,
) -> None:
    db_path = tmp_path / f"{crash_point}.sqlite3"
    runtime = _runtime(frozen_candidate, db_path)
    event = _event(frozen_candidate[2], "crash-event")
    journal = runtime._journal
    original_apply = journal.apply_execution_result

    class SimulatedProcessCrash(BaseException):
        pass

    if crash_point == "before_event_journal":
        def injected_record(*args: Any, **kwargs: Any) -> None:
            raise SimulatedProcessCrash

        journal.record_incoming_runtime_event = injected_record  # type: ignore[method-assign]
    elif crash_point == "after_order_journal":
        original_register = journal.register_order

        def injected_register(*args: Any, **kwargs: Any) -> None:
            original_register(*args, **kwargs)
            raise SimulatedProcessCrash

        journal.register_order = injected_register  # type: ignore[method-assign]
    else:
        def injected_apply(*args: Any, **kwargs: Any) -> bool:
            if crash_point == "before_accounting":
                raise SimulatedProcessCrash
            result = original_apply(*args, **kwargs)
            raise SimulatedProcessCrash

        journal.apply_execution_result = injected_apply  # type: ignore[method-assign]
    with pytest.raises(SimulatedProcessCrash):
        runtime.process_event(event)

    recovered = _runtime(frozen_candidate, db_path)
    result = recovered.process_event(event)
    metrics = recovered.metrics(event.orderbooks[-1])
    reference = _runtime(frozen_candidate, tmp_path / "reference.sqlite3")
    reference_result = reference.process_event(event)
    reference_metrics = reference_result["metrics"]
    assert metrics["cash_krw"] == reference_metrics["cash_krw"]
    assert metrics["positions"]["KRW-BTC"] == reference_metrics["positions"]["KRW-BTC"]
    assert metrics["fees_krw"] == reference_metrics["fees_krw"]
    assert metrics["turnover_krw"] == reference_metrics["turnover_krw"]
    assert metrics["fill_count"] == reference_metrics["fill_count"]
    assert result["event_id"] == event.event_id
