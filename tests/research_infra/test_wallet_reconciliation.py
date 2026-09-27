from __future__ import annotations

from bithumb_coin_trader.research_infra.external_expert import normalize_wallet
from bithumb_coin_trader.research_infra.external_reconstruction import WalletReconciler


def _event(transact_id: str, kind: str, amount: int, status: str, balance: int, minute: int):
    return normalize_wallet({
        "transactid": transact_id,
        "transacttype": kind,
        "transactstatus": status,
        "amount": str(amount),
        "currency": "XBt",
        "walletbalance": str(balance),
        "date": f"2020-01-01T00:{minute:02d}:00Z",
        "timestamp": f"2020-01-01T00:{minute:02d}:00Z",
    })


def test_wallet_reconciliation_excludes_canceled_withdrawals() -> None:
    events = [
        _event("d1", "Deposit", 100, "Completed", 100, 1),
        _event("p1", "RealisedPNL", 50, "Completed", 150, 2),
        _event("w1", "Withdrawal", -20, "Completed", 130, 3),
        _event("w2", "Withdrawal", -7, "Canceled", 130, 4),
    ]

    report = WalletReconciler.reconcile(events, [])

    assert report["summary"]["total_withdrawals_satoshi"] == -20
    assert report["reconciliation_classification"]["cash_flow_continuity"] == "MATCHED"
    assert report["reconciliation_classification"]["completed_wallet_cashflow_sum_satoshi"] == 130
    assert report["reconciliation_classification"]["final_recorded_balance_satoshi"] == 130
    assert report["reconciliation_classification"]["canceled_withdrawal_count_excluded"] == 1
    assert report["reconciliation_classification"]["canceled_withdrawal_amount_satoshi_excluded"] == -7
    assert report["reconciliation_classification"]["trade_pnl_anchor"] == "NOT_IDENTIFIABLE"


def test_wallet_reconciliation_fails_closed_on_unknown_status() -> None:
    events = [
        _event("d1", "Deposit", 100, "Completed", 100, 1),
        _event("w1", "Withdrawal", -7, "Pending", 93, 2),
    ]

    report = WalletReconciler.reconcile(events, [])

    assert report["reconciliation_classification"]["cash_flow_continuity"] == "NOT_VERIFIABLE"
    assert report["reconciliation_classification"]["unverified_status_event_count"] == 1
