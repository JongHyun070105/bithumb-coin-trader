# Execution Simulator & PnL Accounting Audit

**Auditor:** Execution Science — V2 Microstructure Research Program  
**Date:** 2026-09-29  
**Scope:** `execution.py`, `execution_simulator.py`, `TestPreV2Closure` class  
**Files examined:**
- `src/bithumb_coin_trader/research_infra/execution.py` (543 lines)
- `src/bithumb_coin_trader/execution_simulator.py` (632 lines)
- `tests/research_infra/test_research_infra.py` (1752 lines, focus: `TestPreV2Closure` lines 1204–1749)

---

## Issue 1: PnL Double-Counting

**Verdict: ✅ PASS**  
**Severity: —**

### Evidence

The authoritative PnL formula is implemented in two places and both are correct:

**`_close_position` (execution.py, lines 451–465):**
```python
entry_fee = self._position.cumulative_fee
exit_fee = sell_trade.fee_krw
gross_pnl = ((sell_trade.fill_price - self._position.entry_price)
              * sell_trade.fill_quantity)
net_pnl = gross_pnl - entry_fee - exit_fee
```
→ `net = (exit_vwap - entry_vwap) × Q_closed − entry_fee − exit_fee`

**`get_pnl_summary` (execution.py, lines 517–526):**
```python
g = (sell.fill_price - buy.fill_price) * sell.fill_quantity
gross_pnl += g
# Only subtract FEES from VWAP-to-VWAP gross.
# Spread/depth are already in the VWAPs.
net_pnl += g - buy.fee_krw - sell.fee_krw
```

Both paths subtract ONLY fees from VWAP-to-VWAP gross. The `total_cost_krw` field (which bundles half-spread + latency cost + depth cost + fee) is tracked for diagnostic decomposition but is **never subtracted from PnL**.

**Test coverage:** `test_03_net_equals_gross_minus_fees_only` (line 1282) and `test_04_no_spread_double_count` (line 1300) directly verify this invariant.

---

## Issue 2: Fee Sign

**Verdict: ✅ PASS**  
**Severity: —**

### Evidence

**Fee computation (execution_simulator.py, line 419):**
```python
fee_paid = total_filled_krw * request.fee_rate
```

**Fee rate constraint (execution_simulator.py, lines 155–156):**
```python
if self.fee_rate < 0:
    raise ValueError("fee_rate must be non-negative.")
```

**Fee application in PnL (execution.py, line 462):**
```python
net_pnl = gross_pnl - entry_fee - exit_fee
```

Both `entry_fee` and `exit_fee` are non-negative (product of positive notional × non-negative rate) and are correctly **subtracted** from gross PnL.

**Test coverage:** `test_06_positive_entry_fee` (line 1359), `test_07_positive_exit_fee` (line 1375), `test_08_both_fees_included_in_pnl` (line 1392). Note: these assert `>= 0` rather than `> 0`, which is correct since the default regime has `fee_rate=0.0`. When `fee_rate=0.0025` (as in the test), fees are strictly positive.

---

## Issue 3: Latency Book Selection

**Verdict: ✅ PASS**  
**Severity: —**

### Evidence

**`execute_signal` (0ms path, execution.py lines 168–260):**
Uses the signal event's own orderbook directly. No future book lookup. Correct for 0ms theoretical latency.

**`execute_signal_with_latency` (execution.py, lines 262–412):**
```python
latency_ms = self.assumptions.latency_ms          # line 296
signal_ts = signal_event.ordering_timestamp_ns     # line 297
target_fill_ts = signal_ts + int(latency_ms * 1_000_000)  # line 298

# Fill book: first at or after target_fill_ts         # line 317
idx_fill = bisect_left(ob_timestamps, target_fill_ts) # line 318
```

- **0ms:** `target_fill_ts = signal_ts` → `bisect_left` finds the signal book itself. ✓
- **100ms:** `target_fill_ts = signal_ts + 100_000_000` → first book at/after that. ✓
- **250ms/500ms:** Same logic with larger offsets. ✓

**Validation guard (execution.py, line 324):**
```python
if fill_event.ordering_timestamp_ns < signal_ts:
    return None
```

**Low-level `execute_with_latency` (execution_simulator.py, lines 468–569):**
Same algorithm: finds first snapshot ≥ `target_fill_sec`. Also checks for staleness against `max_book_age_ms`.

**Test coverage:** `test_16_100ms_picks_first_book_after_target` (line 1540), `test_17_250ms_picks_first_book_after_target` (line 1567), `test_18_500ms_picks_first_book_after_target` (line 1590), `test_19_never_select_pre_arrival_book` (line 1611), `test_20_no_book_within_tolerance_rejects` (line 1634).

---

## Issue 4: No Impossible Shorts

**Verdict: ✅ PASS**  
**Severity: —**

### Evidence

**`execute_signal` guard (execution.py, lines 186–187):**
```python
if signal == "SELL" and self._position.is_flat:
    return None  # No position to sell (long-only spot)
```

**`execute_signal_with_latency` double-guard (execution.py, lines 292–294):**
```python
if signal == "SELL" and self._position.is_flat:
    return None
if signal == "SELL" and self._position.entry_quantity <= 0:
    return None
```

**BUY guard prevents double-entry (execution.py, lines 183–184):**
```python
if signal == "BUY" and not self._position.is_flat:
    return None  # Already in position
```

**Test coverage:** `test_long_only_no_short` (line 1154), `test_24_h2_no_short_simulation` (line 1715). Both confirm SELL-when-flat returns `None`.

---

## Issue 5: Partial Fills

**Verdict: ⚠️ MINOR ISSUE**  
**Severity: IMPORTANT**

### Evidence

**Low-level residual tracking — CORRECT (execution_simulator.py):**

For `requested_amount_krw` mode (lines 333–335):
```python
unfilled_krw = remaining_krw
unfilled_qty = (remaining_krw / levels[-1][0]) if levels and remaining_krw > 0 else 0.0
is_partial = unfilled_krw > 1e-6
```

For `requested_quantity_btc` mode (lines 356–358):
```python
unfilled_qty = remaining_qty
unfilled_krw = remaining_qty * (levels[-1][0] if levels else 0.0)
is_partial = unfilled_qty > 1e-8
```

Status correctly set (line 437):
```python
status = "PARTIALLY_FILLED" if is_partial else "FILLED"
```

**⚠️ Position tracking on partial SELL — INCORRECT (execution.py, lines 256–257):**

```python
else:  # SELL (exit long)
    self._close_position(trade)
```

And `_close_position` unconditionally sets flat (line 464):
```python
self._position = PositionState(is_flat=True)
```

**Problem:** If a SELL partially fills (e.g., position has 1 BTC but only 0.5 BTC was filled against available bid depth), the code:
1. Correctly computes PnL on the filled portion using `sell_trade.fill_quantity`
2. **Incorrectly marks position as flat**, discarding the remaining 0.5 BTC position

The unfilled residual BTC is "orphaned" — never tracked, never closed.

**Mitigation in practice:** Default test configurations use small position sizes (~100,000 KRW ≈ 0.001 BTC) against books with ≥3 BTC depth per level, making partial SELL fills extremely unlikely. The `SELL` request uses `requested_quantity_btc=self._position.entry_quantity` which matches exactly what was bought, so if the buy-side book depth equals the sell-side book depth, both legs fill symmetrically.

**Test coverage gap:** `test_13_partial_fill_tracks_residual` (line 1482) tests partial fills only at the `DeterministicTakerSimulator` level, not the `ResearchExecutionSimulator` position-tracking level. No test exercises partial SELL fills through the full wrapper.

### Recommendation

`_close_position` should check whether `sell_trade.fill_quantity < self._position.entry_quantity` and, if so, reduce the position rather than clearing it:

```python
def _close_position(self, sell_trade: SimulatedTrade) -> None:
    entry_fee = self._position.cumulative_fee  # full entry fee
    exit_fee = sell_trade.fee_krw
    closed_qty = sell_trade.fill_quantity
    remaining_qty = self._position.entry_quantity - closed_qty

    gross_pnl = (sell_trade.fill_price - self._position.entry_price) * closed_qty
    net_pnl = gross_pnl - entry_fee - exit_fee
    self._initial_equity += net_pnl

    if remaining_qty > 1e-10:
        # Partial close: reduce position
        self._position = PositionState(
            is_flat=False,
            entry_price=self._position.entry_price,
            entry_quantity=remaining_qty,
            entry_notional=self._position.entry_price * remaining_qty,
            entry_timestamp_ns=self._position.entry_timestamp_ns,
            cumulative_fee=0.0,  # fee already accounted
        )
    else:
        self._position = PositionState(is_flat=True)
    self._equity_curve.append((sell_trade.timestamp_ns, self._initial_equity))
```

---

## Issue 6: Additional Impact Separation

**Verdict: ℹ️ ADVISORY**  
**Severity: ADVISORY**

### Evidence

**Declaration (execution.py, lines 43–49):**
```python
Note: ``additional_impact_bps`` models extra market impact beyond what the
depth-walking VWAP already captures.  It is NOT a duplicate of spread or
depth slippage.
```
```python
additional_impact_bps: float  # Extra impact beyond depth-walking VWAP
```

**Actual usage:** `additional_impact_bps` is stored in `ExecutionAssumptions`, persisted in manifests, and included in `to_dict()` / `from_dict()` round-trips. However, it is **never applied** to execution results, fill prices, cost decomposition, or PnL anywhere in the codebase.

- `execute_signal` / `execute_signal_with_latency`: Do not reference `additional_impact_bps`
- `_build_trade`: Does not apply it to `SimulatedTrade` fields
- `_close_position` / `get_pnl_summary`: Do not include it
- `DeterministicTakerSimulator.execute_order`: Does not receive or apply it
- `ExecutionResult.total_cost_krw` = `half_spread + latency + depth + fee` — no additional impact term

**Separation status:** The separation is absolute — so absolute that the parameter has zero effect on any numerical output. A researcher setting `additional_impact_bps=50.0` would get identical execution results and PnL to one setting `additional_impact_bps=0.0`.

**Risk:** A researcher might assume the declared 5bps (default) or 10bps (stress) additional impact is reflected in PnL numbers, when it is not. The PnL is therefore potentially optimistic by the declared additional impact amount.

### Recommendation

Either:
1. **Wire it in:** Apply as a post-VWAP adjustment: `adjusted_pnl = net_pnl - (additional_impact_bps / 10_000) * notional`
2. **Document clearly:** Add a warning in the class docstring that this parameter is for manifest annotation only and is NOT applied to computed PnL
3. **Remove it:** If it's not needed, remove the field to prevent false assumptions

---

## Additional Observations

### O1: `spread_at_fill_bps` incorrectly assigned

**Severity: MINOR**

In `_build_trade` (execution.py, line 442):
```python
spread_at_fill_bps=result.slippage_vs_mid_bps,  # ← should be spread_bps
```

The field `spread_at_fill_bps` is set to `slippage_vs_mid_bps`, but slippage vs mid includes both half-spread and depth walking, while "spread at fill" should be `(best_ask - best_bid) / mid * 10_000`. This conflates spread with total slippage. The `OrderBookSnapshot.spread_bps` property (execution_simulator.py, line 109) exists but is not used here.

### O2: Test `test_09_mid_reference_separate_from_executable` has fragile assertion

**Severity: ADVISORY**

Line 1432:
```python
self.assertNotAlmostEqual(pnl["gross_pnl"], mid_return, places=0)
```

With `places=0`, this asserts the values differ by at least 0.05. With the given wide spread (20,000 KRW), this passes robustly, but the test name suggests it should verify the separation more precisely.

### O3: Equity curve only updated on round-trip close

**Severity: ADVISORY**

`_equity_curve` is only appended in `_close_position`. If the simulation ends with an open position, the final equity state is not recorded. Callers must separately call `get_unrealized_pnl()` for end-of-simulation valuations.

---

## Summary Table

| # | Issue | Verdict | Severity | Lines |
|---|-------|---------|----------|-------|
| 1 | PnL double-counting | ✅ PASS | — | exec.py:460-462, 521-525 |
| 2 | Fee sign | ✅ PASS | — | sim.py:419, exec.py:462 |
| 3 | Latency book selection | ✅ PASS | — | exec.py:296-318, 324 |
| 4 | No impossible shorts | ✅ PASS | — | exec.py:186-187, 292-294 |
| 5 | Partial fill residual | ⚠️ ISSUE | IMPORTANT | exec.py:256-257, 464 |
| 6 | Additional impact | ℹ️ ADVISORY | ADVISORY | exec.py:49 (never applied) |

**Overall assessment:** The core PnL accounting is scientifically correct. The VWAP-to-VWAP minus-fees-only formula is properly implemented in both the position tracker and the summary generator. The critical finding is that partial SELL fills silently discard the unfilled position residual. The `additional_impact_bps` parameter is a latent source of confusion — declared but inert. Both are fixable without restructuring.
