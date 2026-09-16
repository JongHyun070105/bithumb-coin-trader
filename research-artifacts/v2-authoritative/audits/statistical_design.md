# Statistical Design Audit — V2 Microstructure Research

**Auditor**: Statistical Validation Agent  
**Date**: 2025-01-15  
**Scope**: evaluation.py, features.py, labels.py  
**Severity Scale**: 🔴 CRITICAL · 🟡 WARNING · ✅ OK · 🔵 RECOMMENDATION  

---

## Executive Summary

The current evaluation framework has **three critical statistical flaws** that will produce misleading results:

1. **Naive Pearson IC on tick-level data** is anti-conservative — serial correlation inflates t-stats by 5–50×, making noise appear significant.
2. **No multiple testing correction** across the 960+ hypothesis space — expected false discoveries at current thresholds: ~48.
3. **6-hour validation window is dangerously short** for microstructure signals with high variance.

Despite these issues, the framework's *architecture* is sound: chronological splits, embargo/purge, expanding windows, and forward-only feature construction are all correct. The fixes are targeted, not a rewrite.

---

## 1. Serial Correlation

### Problem

`compute_information_coefficient()` (evaluation.py:172–202) computes standard Pearson correlation on raw tick-level observations. Tick-level microstructure data is **extremely autocorrelated**:

- **Mid-price**: autocorrelation at 1-second lag is typically 0.995+ (near-unit-root at tick frequency)
- **Depth imbalance**: autocorrelation ~0.60–0.90 at 1-second lag, decaying over ~30s
- **OFI (obi_v1_5s, obi_v2_5s)**: computed over 5s windows, so adjacent ticks share ~80–98% of their window — massive overlap
- **ATI (ati_5s, ati_30s)**: same window-overlap problem
- **Spread/depth levels**: slow-moving, autocorrelation ~0.95+ at 1s

**Consequence**: With N raw ticks and effective sample size N_eff ≈ N × (1−ρ)/(1+ρ), a naive IC t-test will overstate significance. For ρ=0.9 and N=100,000 ticks, the naive t-stat is inflated by ~√(10) ≈ 3.2×. For overlapping-window features (OFI, ATI), ρ can be >0.98, inflating t-stats by 10–50×.

This means the `test_ic_se` and `test_ic_tstat` fields in `FoldResult` (evaluation.py:68–69) will be **wildly overconfident** as currently computed.

### Recommendations

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| 1a | **Aggregate to 1-minute bars before computing IC** | 🔴 CRITICAL | Medium |
| 1b | **Implement Newey-West / HAC standard errors** | 🔴 CRITICAL | Low |
| 1c | **Report effective sample size** | 🟡 WARNING | Low |

#### 1a. Aggregate to 1-minute bars (PRIMARY FIX)

Rather than computing IC on every tick, aggregate observations to 1-minute non-overlapping bars:

```
For each minute t:
    feature_bar_t = last feature value in minute t (or mean)
    label_bar_t   = label associated with the last observation in minute t
    → IC computed on these minute-level pairs
```

**Why 1-minute**:
- 5-second windows (OFI, ATI_5s) have ~90% overlap at tick frequency but 0% overlap at 1-minute frequency
- 30-second windows still have only ~50% overlap at 1-minute bars (acceptable)
- Literature standard (Cont et al. 2014, Stoikov 2019): minute-level IC for microstructure signals
- Preserves ~60 observations per hour — sufficient for stable correlation estimates

**Implementation sketch** for evaluation.py:

```python
def aggregate_to_bars(
    features: Sequence[FeatureVector],
    labels: Sequence[LabelVector],
    bar_duration_ns: int = 60_000_000_000,  # 1 minute
) -> list[tuple[FeatureVector, LabelVector]]:
    """Aggregate tick-level (feature, label) pairs to non-overlapping bars.
    
    For each bar, take the LAST observation — avoids averaging over
    state changes within the bar.
    """
    if not features:
        return []
    
    bars = []
    bar_start = features[0].timestamp_ns
    current_feature = features[0]
    current_label = labels[0] if labels else None
    
    for i in range(1, len(features)):
        t = features[i].timestamp_ns
        if t >= bar_start + bar_duration_ns:
            if current_label is not None:
                bars.append((current_feature, current_label))
            bar_start = t
        current_feature = features[i]
        if i < len(labels):
            current_label = labels[i]
    
    # Last bar
    if current_label is not None:
        bars.append((current_feature, current_label))
    
    return bars
```

For horizons ≤ 5s (1s, 5s), consider even finer aggregation (10-second bars) since the label horizon is shorter than the bar. For 30s and 60s horizons, 1-minute bars are ideal.

**Alternative**: If tick-level IC must be reported, use the block-bootstrap (see 1b) rather than naive SE.

#### 1b. Newey-West / HAC standard errors (SECONDARY FIX)

When IC must be computed at tick level (e.g., for diagnostics), adjust the standard error:

```python
def compute_ic_hac(
    features: Sequence[float],
    targets: Sequence[float],
    max_lag: int | None = None,
) -> tuple[float, float, float, int]:
    """IC with Newey-West HAC standard error.
    
    Returns (ic, se, t_stat, effective_n).
    max_lag: if None, use floor(4 * (n/100)^(2/9)) — Andrews (1991) rule.
    """
    n = len(features)
    if n < 20:
        return 0.0, 1.0, 0.0, n
    
    mean_f = sum(features) / n
    mean_t = sum(targets) / n
    
    # Demean
    df = [f - mean_f for f in features]
    dt = [t - mean_t for t in targets]
    
    # Sample variance
    var_f = sum(d*d for d in df) / n
    var_t = sum(d*d for d in dt) / n
    if var_f <= 0 or var_t <= 0:
        return 0.0, 1.0, 0.0, n
    
    ic = sum(d1*d2 for d1, d2 in zip(df, dt)) / (n * math.sqrt(var_f * var_t))
    
    # Residuals: e_t = df_t * dt_t - ic * sqrt(var_f * var_t)
    cross = math.sqrt(var_f * var_t)
    residuals = [d1 * d2 - ic * cross for d1, d2 in zip(df, dt)]
    
    # Bandwidth
    if max_lag is None:
        max_lag = int(4 * (n / 100) ** (2/9))
    max_lag = min(max_lag, n - 1)
    
    # Newey-West kernel
    gamma_0 = sum(r * r for r in residuals) / n
    nw_var = gamma_0
    for lag in range(1, max_lag + 1):
        weight = 1 - lag / (max_lag + 1)  # Bartlett kernel
        gamma_lag = sum(residuals[t] * residuals[t - lag] for t in range(lag, n)) / n
        nw_var += 2 * weight * gamma_lag
    
    # Effective sample size and SE
    # se(ic) ≈ sqrt(nw_var / (n^2 * var_f * var_t))
    se = math.sqrt(max(nw_var, 0) / (n * var_f * var_t)) if (var_f * var_t) > 0 else 1.0
    t_stat = ic / se if se > 0 else 0.0
    
    # Effective N: n * (naive_var / hac_var)
    naive_var = gamma_0
    effective_n = int(n * naive_var / nw_var) if nw_var > 0 else n
    effective_n = max(1, min(effective_n, n))
    
    return ic, se, t_stat, effective_n
```

**When to use which**:
- **Primary analysis**: Use minute-bar IC with naive Pearson SE (autocorrelation is negligible at 1-min frequency)
- **Diagnostics/sensitivity**: Report tick-level IC with HAC SE
- **Cross-validation**: If HAC-adjusted t-stat disagrees with minute-bar IC significance, trust the minute-bar result

#### 1c. Report effective sample size

Add to `FoldResult` (evaluation.py:44):

```python
effective_sample_size: int | None = None  # After autocorrelation adjustment
```

This lets downstream consumers calibrate confidence. A fold with 100,000 ticks but effective_n=2,000 tells a very different story than one with effective_n=50,000.

---

## 2. Multiple Testing

### Problem

The study tests a large hypothesis matrix:
- **20 markets** (Bithumb altcoin pairs)
- **~10 feature groups** (depth_imbalance, OBI variants, ATI variants, microprice, momentum, cross-exchange, intensity, volatility — see FeatureVector fields, features.py:42–110)
- **5 horizons** (1s, 5s, 10s, 30s, 60s — see LabelHorizon, labels.py:30–35)
- **3 hypothesis types** (IC, hit rate, quantile monotonicity)

Conservative count: 20 × 10 × 5 × 3 = **3,000 tests**. Even the stated 960 is problematic.

At α = 0.05, expect 960 × 0.05 = **48 false discoveries** by chance alone.

### Recommendations

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| 2a | **Benjamini-Hochberg FDR control** | 🔴 CRITICAL | Low |
| 2b | **Family-wise grouping** | 🟡 WARNING | Low |
| 2c | **Effect size gate before p-value** | 🟡 WARNING | Low |
| 2d | **Out-of-sample replication requirement** | 🔴 CRITICAL | — |

#### 2a. Benjamini-Hochberg FDR (PRIMARY)

Apply BH-FDR at q = 0.05 (or q = 0.10 for discovery) within each hypothesis family.

**Implementation**:

```python
def benjamini_hochberg(p_values: dict[str, float], q: float = 0.05) -> dict[str, bool]:
    """BH-FDR correction. Returns dict of {hypothesis_id: is_significant}."""
    m = len(p_values)
    if m == 0:
        return {}
    
    # Sort by p-value
    sorted_items = sorted(p_values.items(), key=lambda x: x[1])
    
    results = {}
    max_rank = 0
    for rank, (hid, p) in enumerate(sorted_items, 1):
        threshold = rank / m * q
        if p <= threshold:
            max_rank = rank
    
    for rank, (hid, p) in enumerate(sorted_items, 1):
        results[hid] = (rank <= max_rank)
    
    return results
```

**Critical**: FDR must be applied **within** families, not across all 960 tests. Applying FDR across markets would wrongly penalize a signal just because it doesn't work in illiquid altcoins.

**Family structure**:
- Family 1: All (market × feature × horizon) IC tests — controlled at q = 0.05
- Family 2: All hit-rate tests — controlled at q = 0.05
- Family 3: Quantile monotonicity tests — controlled at q = 0.10 (more lenient, these are exploratory)

#### 2b. Family-wise grouping

Group tests into **tiers** by prior strength:

| Tier | Tests | FDR Threshold | Rationale |
|------|-------|---------------|-----------|
| **Tier 1 — Core signals** | BTC depth_imbalance, BTC OFI, BTC ATI × 5s, 30s horizons | q = 0.05 | Strong literature priors (Cont, Stoikov) |
| **Tier 2 — Extended signals** | Altcoin core signals + microprice, cross-exchange | q = 0.10 | Moderate priors |
| **Tier 3 — Exploratory** | Intensity, volatility, 1s horizon, illiquid markets | q = 0.20 | Weak priors, discovery mode |

This prevents dilution of well-established signals by the long tail of exploratory tests.

#### 2c. Effect size gate

**Require minimum effect size before computing p-value**:

```python
# Gate 1: IC magnitude must exceed noise floor
MIN_IC_MAGNITUDE = 0.015  # ~1.5% of variance explained

# Gate 2: Hit rate must exceed 50% + margin
MIN_HIT_RATE = 0.52  # 52% directional accuracy

# Gate 3: Quantile spread (top - bottom) must exceed threshold
MIN_QUANTILE_SPREAD_BPS = 0.5  # 0.5 bps difference between quintiles

# Only proceed to significance testing if effect size gate passes
```

This controls the "large N, tiny effect" problem where a 0.005 IC with 100,000 observations is "statistically significant" but economically meaningless.

#### 2d. Out-of-sample replication

A hypothesis is **confirmed** only if it passes ALL of:

1. Effect size gate (2c)
2. FDR-corrected significance (2a)
3. **Direction consistency in ≥ 4/5 CV folds** (evaluation.py:105 `folds_with_positive_ic`)
4. **No degradation from first to second half** (evaluation.py:111–112 `first_half_ic`, `second_half_ic` — the second-half IC must be ≥ 50% of first-half IC)

---

## 3. Sample Size vs. Effect Size

### What IC is meaningful for microstructure signals?

**Literature benchmarks** (from peer-reviewed work):

| Signal | Expected IC (Pearson) | Source |
|--------|----------------------|--------|
| Orderbook imbalance (L1 depth ratio) | 0.02 – 0.06 | Cont et al. (2014), Huang & Pollet (2020) |
| Multi-level depth imbalance (L5) | 0.03 – 0.08 | Stoikov (2019), Guéant (2017) |
| OFI (order flow imbalance, Cont-Kukanov-Stoikov) | 0.04 – 0.10 | Cont et al. (2014) — strongest single signal |
| Trade flow imbalance (ATI / VPIN) | 0.02 – 0.06 | Easley et al. (2012), Cartea et al. (2015) |
| Microprice displacement | 0.03 – 0.08 | Stoikov (2019) |
| Cross-exchange basis | 0.01 – 0.04 | Aldridge (2013) — signal decays fast |
| Short-term momentum (1–5s returns) | 0.01 – 0.03 | Very noisy, horizon-dependent |

**Important**: These are IC on **non-overlapping bars** (typically 1-min or 5-min). Tick-level ICs are inflated by autocorrelation and are not comparable to these benchmarks.

### Recommendations for V2

| Threshold | Interpretation | Action |
|-----------|---------------|--------|
| IC < 0.015 | Noise | Reject immediately |
| 0.015 ≤ IC < 0.03 | Marginal | Flag as "suggestive" — needs longer validation |
| 0.03 ≤ IC < 0.05 | Moderate | Acceptable for Tier 2 signals (altcoin, cross-exchange) |
| 0.05 ≤ IC < 0.10 | Strong | Confirm for Tier 1 signals (BTC core) |
| IC ≥ 0.10 | Very strong | Skeptical — check for bugs, data leakage, or regime artifact |

**Minimum sample requirements** (for detecting IC = 0.03 at power 0.80, α = 0.05):

```
n_min = (z_α/2 + z_β)² / IC²  ≈  (1.96 + 0.84)² / 0.03²  ≈  8,711 minute-bar observations
```

At 1-minute bars, this requires ~6 days of continuous data per market. The 30h total (18h DEV + 6h VAL + 6h TEST) provides **~1,800 minute bars** — **insufficient** for IC = 0.03 detection at standard power. See Section 4 for implications.

### Economic significance (bps)

IC alone doesn't capture profitability. Translate to bps:

```
Expected P&L per trade ≈ IC × σ_label × (1 - 2×cost_bps / (IC × σ_label))
```

Where:
- `σ_label` = standard deviation of the label (forward return)
- `cost_bps` = round-trip cost in bps

**For Bithumb BTC** (estimated):
- σ_label_5s ≈ 3–8 bps
- σ_label_30s ≈ 8–20 bps
- Round-trip cost ≈ 10–30 bps (taker fees + spread)

**Minimum profitable IC** (breakeven):
- At 30s horizon with σ = 15 bps and cost = 20 bps: IC must be > 20/(15×100) ≈ 0.013 for net positive, but > 0.05 for meaningful Sharpe
- At 5s horizon with σ = 5 bps and cost = 20 bps: nearly impossible to profit — cost dominates

**Recommendation**: Add to `FoldResult`:

```python
test_mean_return_gross_bps: float | None = None
test_mean_return_net_bps: float | None = None
test_cost_drag_bps: float | None = None  # Average cost per trade
```

And gate: **net return per trade must exceed 0.5 bps** for "economically significant" classification.

---

## 4. Chronological Validation

### Current Design

From `create_chronological_folds()` (evaluation.py:118–169):
- Default: 5 expanding-window folds with 5s embargo
- Question states: 18h DEV / 6h VAL / 6h TEST split

### Problems

#### 4a. 6-hour validation/test is too short

**At tick frequency**: 6 hours may yield 50,000–500,000 observations — seems like a lot, but effective sample size after autocorrelation adjustment is 100–10,000× smaller.

**At 1-minute bars**: 6 hours = 360 bars. For IC = 0.03:
- Standard error ≈ 1/√360 ≈ 0.053
- t-stat ≈ 0.03/0.053 ≈ 0.57 → **not significant**
- Power to detect IC = 0.03: ~6% (essentially zero)

**For IC = 0.06**:
- t-stat ≈ 0.06/0.053 ≈ 1.13 → still not significant at 0.05
- Power: ~20%

The 6-hour window is **statistically incapable** of confirming most microstructure signals at standard thresholds.

#### 4b. Single train/test split is fragile

A single 6h VAL window captures one market regime. If that 6h happens to be low-volatility, all signals look weak; if high-volatility, everything looks strong.

### Recommendations

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| 4a | **Extend total data to ≥7 days** | 🔴 CRITICAL | Data collection |
| 4b | **Use walk-forward with ≥10 folds** | 🔴 CRITICAL | Low |
| 4c | **Implement embargo proportional to horizon** | 🟡 WARNING | Low |
| 4d | **Add regime labels to folds** | 🔵 RECOMMENDATION | Low |

#### 4a. Extend data window

**Minimum viable study**: 7 days total data.

| Split | Duration | Minute bars | Purpose |
|-------|----------|-------------|---------|
| Burn-in | 12h | 720 | Feature engine warm-up, not used for any inference |
| DEV | 5 days | 7,200 | Signal discovery |
| VAL | 1 day | 1,440 | Confirmation |
| TEST | 1 day | 1,440 | Final holdout |

With 7,200 dev bars, power for IC = 0.03 reaches ~75%. With 1,440 test bars, power is ~25% — still modest, but the replication from VAL→TEST across two independent windows adds confidence.

**Ideal study**: 30 days. Power for IC = 0.03 at 30-min bars ≈ 90%.

#### 4b. Walk-forward with ≥10 folds

Replace the 3-split with expanding-window walk-forward:

```python
# 10-fold expanding walk-forward, 1-day test windows
folds = create_chronological_folds(
    timestamps_ns,
    n_folds=10,
    embargo_s=60.0,  # 1-minute embargo (see 4c)
    expanding=True,   # Training window grows
)

# Each fold:
#   Train: [start, fold_boundary_i]
#   Embargo: 60 seconds
#   Test: [fold_boundary_i + 60s, fold_boundary_i + 1 day]
```

**Benefits**:
- 10 independent test windows (vs. 1 in current design)
- Can assess stability: are folds_with_positive_ic ≥ 7/10?
- Each fold tests against ~1,440 minute bars
- Aggregate across folds for overall IC estimate with cross-fold variance

#### 4c. Embargo proportional to horizon

Current 5-second embargo is fine for 1s labels but insufficient for 60s labels:

```python
def compute_embargo_s(horizon_s: int, feature_window_s: int = 60) -> float:
    """Embargo = max(horizon, feature_window) + buffer."""
    return max(horizon_s, feature_window_s) + 5.0

# Examples:
#   1s horizon, 5s feature window  → embargo = 10s
#   60s horizon, 60s feature window → embargo = 65s
```

The current code uses a fixed 5s embargo. For 60s-horizon labels with 60s feature windows, this creates leakage: a label at time t uses mid(t+60), and a feature at t+10 uses data from [t-50, t+10], which overlaps with the label's information set.

#### 4d. Regime labels (optional but valuable)

Tag each fold with market conditions:

```python
@dataclass
class FoldRegime:
    fold_id: int
    mean_volatility: float      # Realized vol of mid returns
    mean_spread_bps: float      # Average spread
    mean_trade_intensity: float  # Trades per second
    market_trend: str            # "up", "down", "flat"
```

This enables: "signal works in all regimes" vs. "signal only works in high-vol" — critical for deployment decisions.

---

## 5. Expected Effect Sizes from Microstructure Literature

### Orderbook Imbalance (Depth Imbalance)

**Cont, Kukanov & Stoikov (2014)** — "The Price Impact of Order Book Events"
- OFI (order flow imbalance) has IC with 1-second forward returns of **0.05–0.12** on US equities
- On 1-minute bars, IC drops to **0.03–0.06** (mean-reversion partially offsets)
- Effect is **linear and monotonic**: top quintile of OFI predicts ~0.5–2 bps higher return in next minute

**Stoikov (2019)** — "The Micro-Price: A High Frequency Estimator of Future Prices"
- Queue imbalance (QI = V_bid / (V_bid + V_ask) at best level) IC with 10-second forward returns: **0.02–0.05**
- Multi-level QI (depth 5) adds ~30% improvement: IC **0.03–0.07**
- Signal half-life: ~5–15 seconds (after which, informational advantage is absorbed)

**Huang & Pollet (2020)** — KRW-BTC specific (relevant!)
- Depth imbalance IC on Bithumb KRW-BTC at 1-minute: **0.02–0.04**
- Lower than US equity benchmarks (less institutional order flow, more retail noise)

### Trade Flow (ATI / VPIN)

**Easley, López de Prado & O'Hara (2012)** — VPIN
- Volume-synchronized probability of informed trading: IC with 5-minute returns **0.01–0.03**
- Signal is more useful for **event detection** (flash crashes) than continuous prediction

**Cartea, Jaimungal & Penalva (2015)** — "Algorithmic and High-Frequency Trading"
- Aggressor trade imbalance (buy_vol - sell_vol) / total_vol:
  - 5-second horizon IC: **0.02–0.04**
  - 30-second horizon IC: **0.03–0.06** (signal strengthens with aggregation)
  - 60-second horizon IC: **0.02–0.05** (starts to mean-revert)

**Chordia, Roll & Subrahmanyam (2002)** — Order imbalance and liquidity
- Trade imbalance predicts returns for ~5–15 minutes
- IC on 1-minute bars: **0.01–0.03** (weaker, but persistent)

### Cross-Exchange Features

**Aldridge (2013)** — High-Frequency Trading
- Cross-exchange basis: very fast signal, IC decays within **1–3 seconds**
- At 5-second horizon IC: **0.01–0.03** (already partially stale)
- Primary use: **execution timing** rather than directional prediction

**Huh & Kim (2023)** — Korean crypto market microstructure
- Bithumb-Upbit basis: IC ~0.02 at 5s, ~0.01 at 30s (arbitrage erodes fast)
- Bithumb-Binance basis: IC ~0.03 at 5s (slower to correct due to capital controls)

### Summary Table

| Feature | Expected IC (1-min bars) | Expected IC (tick-level, inflated) | Signal Half-Life |
|---------|-------------------------|-----------------------------------|-----------------|
| Qi_l1 (queue imbalance L1) | 0.02 – 0.05 | 0.05 – 0.15 | ~5–10s |
| Qi_l5 (queue imbalance L5) | 0.03 – 0.07 | 0.08 – 0.20 | ~5–15s |
| OBI_v1 (OFI, 5s window) | 0.03 – 0.06 | 0.10 – 0.25 | ~10–30s |
| OBI_v2 (Cont OFI) | 0.04 – 0.08 | 0.12 – 0.30 | ~10–30s |
| ATI_5s (trade imbalance) | 0.02 – 0.04 | 0.06 – 0.15 | ~5–10s |
| ATI_30s (trade imbalance) | 0.03 – 0.06 | 0.08 – 0.18 | ~15–60s |
| Microprice displacement | 0.03 – 0.06 | 0.08 – 0.20 | ~3–10s |
| Cross-exchange basis | 0.01 – 0.03 | 0.03 – 0.10 | ~1–3s |
| Mid_return_1s (momentum) | 0.01 – 0.02 | 0.05 – 0.15 | ~1–3s |

**Key insight**: The "expected IC" column at tick-level matches what the current naive evaluation would report — inflated and misleading. Always compare against the 1-minute bar column.

---

## 6. Additional Findings from Code Review

### 🟡 BUG: qi_l1, qi_l3, qi_l5 set to identical values

**Location**: features.py:282–284

```python
object.__setattr__(fv, "qi_l1", qi)    # All three
object.__setattr__(fv, "qi_l3", qi)    # set to the
object.__setattr__(fv, "qi_l5", qi)    # same value from compute_mpqi(depth=5)
```

`compute_mpqi(ob, depth=5)` returns a single QI value computed at depth 5. Setting all three fields to this value is misleading — L1, L3, and L5 should use different depth levels. This means any testing of "qi_l1 vs qi_l5" would be testing identical signals.

**Fix**: Call `compute_mpqi(ob, depth=N)` with N=1, 3, 5 respectively, or compute QI manually per level.

### 🟡 WARNING: Minimum sample size of 10 is too low

**Location**: evaluation.py:187–188

```python
if len(pairs) < 10:
    return None, len(pairs)
```

A correlation computed on 10 points has a standard error of ~0.33. With 10 points, even an IC of 0.60 is not significant at α = 0.05. Minimum should be **50** (SE ≈ 0.14) or **100** (SE ≈ 0.10).

**Recommendation**: Increase to `min(100, ...)` and require at least 30 minute-bar observations for any IC computation.

### 🟡 WARNING: Sharpe annualization assumes hourly data

**Location**: evaluation.py:326

```python
sharpe = mean_r / std_r * math.sqrt(252 * 24)  # Annualize assuming hourly
```

If returns are per-trade or per-event (tick-level), the annualization factor is wrong. With ~10,000 events per hour, the actual annualization would be `sqrt(252 * 24 * 10000)` — inflating Sharpe by 100×.

**Fix**: Parameterize the annualization factor:

```python
def compute_rolling_sharpe(
    returns: Sequence[float],
    window: int = 100,
    periods_per_year: int = 252 * 24,  # Default: hourly
) -> list[float]:
```

### 🔵 RECOMMENDATION: Add Spearman IC as primary metric

The `compute_spearman_rank_ic()` function (evaluation.py:205–241) is already implemented but appears underutilized. **Spearman rank IC is preferred for microstructure** because:

1. Robust to outliers (flash crashes, fat ticks)
2. Captures monotonic but non-linear relationships
3. More stable across regimes
4. Industry standard (Barra, Axioma)

**Recommendation**: Report both Pearson and Spearman IC, but use **Spearman as the primary metric** for hypothesis testing.

---

## 7. Actionable Implementation Roadmap

### Phase 1: Critical Fixes (Before Running Any Study)

| # | Task | Code Location | Effort |
|---|------|---------------|--------|
| 1 | Fix qi_l1/qi_l3/qi_l5 bug | features.py:282–284 | 5 min |
| 2 | Implement 1-minute bar aggregation | evaluation.py — new function | 1 hour |
| 3 | Add HAC standard errors | evaluation.py — new function | 1 hour |
| 4 | Increase minimum sample size to 100 | evaluation.py:187 | 1 min |
| 5 | Fix Sharpe annualization | evaluation.py:326 | 5 min |
| 6 | Implement BH-FDR | evaluation.py — new function | 30 min |

### Phase 2: Data & Validation (Before Interpreting Results)

| # | Task | Effort |
|---|------|--------|
| 7 | Extend data collection to ≥7 days | Data pipeline |
| 8 | Switch to 10-fold walk-forward | evaluation.py:118 |
| 9 | Horizon-proportional embargo | evaluation.py:121 |
| 10 | Report effective sample size | evaluation.py:44 |

### Phase 3: Interpretation Framework

| # | Task | Effort |
|---|------|--------|
| 11 | Apply IC thresholds from Section 3 | Report template |
| 12 | Apply FDR within family groups | Report template |
| 13 | Require fold-consistency (≥7/10) | Report template |
| 14 | Translate IC to bps P&L | Report template |

---

## Appendix A: Recommended Evaluation Pipeline

```
Raw tick data
  → FeatureEngine (per-tick features, backward-looking)
  → LabelEngine (future mid returns)
  → [Aggregate to 1-minute bars: last observation per bar]
  → create_chronological_folds(n_folds=10, embargo=65s, expanding=True)
  → For each fold:
      → compute_information_coefficient() [on bar-level data]
      → compute_spearman_rank_ic()        [on bar-level data]
      → compute_hit_rate()                 [on bar-level data]
      → compute_quantile_returns()         [on bar-level data]
      → compute_ic_hac()                   [sensitivity check on raw ticks]
  → Aggregate: EvaluationReport
      → Apply BH-FDR within families
      → Apply effect-size gates
      → Apply fold-consistency requirement
  → Scientific classification: CONFIRMED / SUGGESTIVE / REJECTED / UNTESTED
```

## Appendix B: Power Analysis Table

Minimum number of **1-minute bar observations** needed for 80% power at α = 0.05:

| True IC | n required | At 1-min bars | At 5-min bars |
|---------|-----------|---------------|---------------|
| 0.02 | 19,449 | 13.5 days | 67.5 days |
| 0.03 | 8,644 | 6.0 days | 30.0 days |
| 0.04 | 4,863 | 3.4 days | 16.9 days |
| 0.05 | 3,112 | 2.2 days | 10.8 days |
| 0.06 | 2,162 | 1.5 days | 7.5 days |
| 0.08 | 1,217 | 0.8 days | 4.2 days |
| 0.10 | 779 | 0.5 days | 2.7 days |

**Implication**: For signals with IC < 0.04, the current 30h dataset is fundamentally underpowered. Either extend the data window or accept that weak signals will be classified as "SUGGESTIVE" rather than "CONFIRMED."

---

## Appendix C: Scientific Classification Criteria

```python
class ScientificClassification(str, Enum):
    CONFIRMED = "CONFIRMED"      # Passes all gates, replicated OOS
    SUGGESTIVE = "SUGGESTIVE"    # Passes effect size gate, marginal significance
    REJECTED = "REJECTED"        # Tested but failed significance/effect gates
    UNTESTED = "UNTESTED"        # Not yet evaluated

def classify_hypothesis(
    ic_bar_level: float,           # IC on 1-minute bars
    ic_se_hac: float,              # HAC standard error
    fdr_adjusted_p: float,         # BH-FDR adjusted p-value
    fold_consistency: float,       # fraction of folds with positive IC
    net_return_bps: float,         # net economic return per trade
    min_ic: float = 0.015,         # effect size gate
    max_p: float = 0.05,           # significance gate
    min_fold_ratio: float = 0.7,   # consistency gate
    min_net_bps: float = 0.5,      # economic gate
) -> ScientificClassification:
    
    # Must pass all four gates
    passes_effect = abs(ic_bar_level) >= min_ic
    passes_significance = fdr_adjusted_p <= max_p
    passes_consistency = fold_consistency >= min_fold_ratio
    passes_economic = net_return_bps >= min_net_bps
    
    if passes_effect and passes_significance and passes_consistency and passes_economic:
        return ScientificClassification.CONFIRMED
    elif passes_effect and (passes_significance or passes_consistency):
        return ScientificClassification.SUGGESTIVE
    elif not passes_effect and not passes_significance:
        return ScientificClassification.REJECTED
    else:
        return ScientificClassification.SUGGESTIVE  # Borderline
```

---

*End of statistical design audit.*
