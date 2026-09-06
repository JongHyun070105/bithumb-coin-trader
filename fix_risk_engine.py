import re

with open('src/bithumb_coin_trader/risk_engine.py', 'r') as f:
    content = f.read()

# Add dataclass ExecutionCostEstimate and modify RiskEngineConfig
import_part = '''
from .canonical_market_data import CanonicalOrderBook
from .execution_simulator import OrderBookSnapshot

@dataclass(frozen=True, slots=True)
class ExecutionCostEstimate:
    spread_crossing_bps: float
    depth_slippage_bps: float
    fee_bps: float
    total_execution_cost_bps: float
    fill_ratio: float
    expected_vwap: float
    visible_depth_krw: float

def simulate_taker_execution(
    side: str,
    requested_notional_krw: float,
    levels: tuple[tuple[float, float], ...],
    best_reference_price: float,
    taker_fee_bps: float = 40.0,
) -> ExecutionCostEstimate:
    if not levels or best_reference_price <= 0:
        return ExecutionCostEstimate(99999.0, 99999.0, taker_fee_bps, 99999.0, 0.0, 0.0, 0.0)

    visible_depth_krw = sum(px * sz for px, sz in levels)
    
    filled_notional = 0.0
    filled_size = 0.0
    for px, sz in levels:
        remaining = requested_notional_krw - filled_notional
        if remaining <= 0:
            break
        level_notional = px * sz
        fill_notional = min(remaining, level_notional)
        fill_size = fill_notional / px
        filled_notional += fill_notional
        filled_size += fill_size
        
    fill_ratio = filled_notional / requested_notional_krw if requested_notional_krw > 0 else 0.0
    expected_vwap = filled_notional / filled_size if filled_size > 0 else levels[0][0]
    
    best_px = levels[0][0]
    
    if side == "BUY":
        spread_crossing_bps = (best_px - best_reference_price) / best_reference_price * 10000.0
        depth_slippage_bps = (expected_vwap - best_px) / best_reference_price * 10000.0
    else:  # SELL
        spread_crossing_bps = (best_reference_price - best_px) / best_reference_price * 10000.0
        depth_slippage_bps = (best_px - expected_vwap) / best_reference_price * 10000.0
        
    spread_crossing_bps = max(0.0, spread_crossing_bps)
    depth_slippage_bps = max(0.0, depth_slippage_bps)
    
    total = spread_crossing_bps + depth_slippage_bps + taker_fee_bps
    
    return ExecutionCostEstimate(
        spread_crossing_bps=spread_crossing_bps,
        depth_slippage_bps=depth_slippage_bps,
        fee_bps=taker_fee_bps,
        total_execution_cost_bps=total,
        fill_ratio=fill_ratio,
        expected_vwap=expected_vwap,
        visible_depth_krw=visible_depth_krw,
    )
'''

content = content.replace('from .canonical_market_data import CanonicalOrderBook\nfrom .execution_simulator import OrderBookSnapshot', import_part)

config_part = '''
@dataclass
class RiskEngineConfig:
    max_order_notional_krw: float = 10_000_000.0
    max_portfolio_exposure_fraction: float = 0.95
    max_spread_bps: float = 50.0
    max_slippage_bps: float = 30.0  # BUG-2 FIX: now actually enforced
    taker_fee_bps: float = 40.0
    max_total_execution_cost_bps: float = 80.0
    max_data_age_ms: float = 5000.0
'''
content = re.sub(r'@dataclass\nclass RiskEngineConfig:[\s\S]*?max_data_age_ms: float = 5000\.0', config_part.strip(), content, count=1)


engine_init = '''
    def __init__(self, config: RiskEngineConfig | None = None, audit_sink_path: Path | str | None = None) -> None:
        self.config = config or RiskEngineConfig()
        self.halted: bool = False
        self.halt_reason: str = ""
        self.kill_switch_active: bool = False
        self.consecutive_rejections: int = 0
        self.audit_log: list[RiskAuditRecord] = []  # In-memory; not immutable (see LIMITATION)
        self._audit_sink_path = audit_sink_path
'''
content = re.sub(r'def __init__\(self, config: RiskEngineConfig \| None = None\) -> None:.*?self\.audit_log: list\[RiskAuditRecord\] = \[\][^\n]*', engine_init.strip('\n'), content, flags=re.DOTALL)


finalize_part = '''
        audit = RiskAuditRecord(
            timestamp_ms=timestamp_ms,
            order_id=order_id,
            verdict=verdict,
            reasons=tuple(reasons),
            context_hash=ctx_hash,
        )
        self.audit_log.append(audit)
        
        if self._audit_sink_path:
            with open(self._audit_sink_path, 'a') as f:
                f.write(json.dumps(audit.to_dict()) + '\\n')
                f.flush()
                
        return verdict, tuple(reasons), audit
'''
content = re.sub(r'audit = RiskAuditRecord\([\s\S]*?return verdict, tuple\(reasons\), audit', finalize_part.strip('\n'), content)


preflight_oversell = '''
        # BUG-ADD: reject semantically invalid inputs
        if requested_notional_krw <= 0:
            reasons.append(f"requested_notional_krw must be > 0, got {requested_notional_krw}")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )
        if current_equity_krw <= 0:
            reasons.append(f"current_equity_krw must be > 0, got {current_equity_krw}")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )
        if current_position_notional_krw < 0:
            reasons.append(
                f"current_position_notional_krw < 0 is invalid: {current_position_notional_krw}"
            )
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )
        if side == 'SELL' and requested_notional_krw > current_position_notional_krw:
            reasons.append(f'INSUFFICIENT_POSITION: requested {requested_notional_krw} > position {current_position_notional_krw}')
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.REJECT, reasons, current_time_ms
            )
'''
content = re.sub(r'# BUG-ADD: reject semantically invalid inputs[\s\S]*?RiskVerdict\.HALT, reasons, current_time_ms\n            \)', preflight_oversell.strip('\n'), content)


loss_frac = '''
        # 4. Daily drawdown circuit breaker
        if daily_loss_fraction < 0 or daily_loss_fraction > 1.0:
            reasons.append(f"Invalid daily_loss_fraction: {daily_loss_fraction}")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )
        if daily_loss_fraction >= self.config.max_daily_loss_fraction:
'''
content = content.replace('# 4. Daily drawdown circuit breaker\n        if daily_loss_fraction >= self.config.max_daily_loss_fraction:', loss_frac.strip('\n'))


age_ms_logic = '''
        # Stale data check
        age_ms = current_time_ms - ob_ts_ms
        if age_ms < -50:
            reasons.append(f"CLOCK_INVERSION: Market data is in the future: age {age_ms}ms")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )
        if age_ms > self.config.max_data_age_ms:
'''
content = content.replace('# Stale data check\n        age_ms = current_time_ms - ob_ts_ms\n        if age_ms > self.config.max_data_age_ms:', age_ms_logic.strip('\n'))


slippage_logic = '''
        # BUG-2 FIX: max_slippage_bps is now actually enforced
        estimated_slippage_bps = self._estimate_slippage_bps(
            side, requested_notional_krw, best_bid, best_ask
        )
        if estimated_slippage_bps > self.config.max_slippage_bps:
            reasons.append(
                f"Estimated slippage {estimated_slippage_bps:.2f} bps exceeds "
                f"limit {self.config.max_slippage_bps:.2f} bps"
            )

        mid_price = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else 0.0
        if mid_price > 0 and isinstance(orderbook, CanonicalOrderBook):
            levels = orderbook.asks if side == 'BUY' else orderbook.bids
            if levels:
                cost_estimate = simulate_taker_execution(
                    side, requested_notional_krw, levels, mid_price, self.config.taker_fee_bps
                )
                if cost_estimate.total_execution_cost_bps > self.config.max_total_execution_cost_bps:
                    reasons.append(
                        f"Execution cost {cost_estimate.total_execution_cost_bps:.2f} bps exceeds limit {self.config.max_total_execution_cost_bps:.2f} bps"
                    )
'''
content = re.sub(r'# BUG-2 FIX: max_slippage_bps is now actually enforced[\s\S]*?limit \{self\.config\.max_slippage_bps:\.2f\} bps"\n            \)', slippage_logic.strip('\n'), content)


with open('src/bithumb_coin_trader/risk_engine.py', 'w') as f:
    f.write(content)
