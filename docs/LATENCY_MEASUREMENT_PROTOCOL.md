# Latency Measurement Protocol & Execution Haircut Framework

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-05  
**Execution Environment:** AWS `ap-northeast-2` (Seoul) to Bithumb Korea (`api.bithumb.com` / WebSocket Gateway)

---

## 1. Objective & Scope

This protocol establishes the quantitative and operational methodology for measuring, modeling, and applying execution latency haircuts to trading strategies within the Bithumb Coin Trader repository.

In high-frequency and microstructure trading, latency is the primary driver of adverse selection:
$$\text{Adverse Selection} = \mathbb{E}\left[ P_{t + \tau} - P_t \mid \text{Order Placed at } t \right]$$
where $\tau$ is the stochastic execution delay.

---

## 2. Formal Timestamp Measurement Points

Every simulated or live order transition tracks the following discrete timestamp boundaries (in microsecond precision):

```
Client Application          AWS Kernel / NIC          Public Internet          Bithumb Gateway / Engine
        |                          |                         |                            |
  (t_0) |--- Order Packet Send --->|                         |                            |
        |                          |--- TCP SYN/Data ------->|--------------------------->| (t_recv)
        |                          |<-- TCP ACK -------------|<---------------------------|
  (t_1) |<-- Socket ACK Notification
        |                                                                                 | [Order Matching]
        |                                                                                 | [Execution / Fill]
        |                          |<-- HTTP 200 / OrderResp |<---------------------------| (t_resp_send)
  (t_3) |<-- HTTP Response Recv ---|                         |                            |
        |                                                    |<-- WS Execution Event -----| (t_ws_event)
  (t_4) |<-- WS Fill Event Recv ---|<------------------------|                            |
```

1. **$t_0$ (Client Order Dispatch):** Monotonic system timestamp when `requests.post` or `websocket.send` dispatches the payload to the OS kernel socket buffer.
2. **$t_1$ (Kernel TCP ACK):** Timestamp when the TCP ACK is registered (derived via eBPF/sockstat where instrumented).
3. **$t_2$ (Server Processing Duration):** Server-side processing interval reported in Bithumb API response headers (`Date`, `x-request-time`, or millisecond timestamps inside payload) if provided:
   $$\tau_{\text{server}} = t_{\text{resp\_send}} - t_{\text{recv}}$$
4. **$t_3$ (REST Response Arrival):** Monotonic timestamp when the full HTTP response body is parsed in client memory.
   $$\tau_{\text{REST\_RTT}} = t_3 - t_0$$
5. **$t_4$ (WebSocket Private Execution Notification):** Monotonic timestamp when the private order event / fill stream delivers the execution confirmation.
   $$\tau_{\text{Fill\_Confirm}} = t_4 - t_0$$

---

## 3. Measured vs. Estimated Latency Standard

In compliance with repository data integrity standards:
- **MEASURED LATENCY:** Empirical RTT timestamps recorded directly by client sockets under active production conditions. Must include sample size, timestamp range, and hardware environment.
- **ESTIMATED LATENCY:** Parametric or conservative latency models used during offline backtesting and research simulations. Must be explicitly designated as `ESTIMATED_HAIRCUT`.

---

## 4. Latency Distribution & Modeling

Empirical network latency distributions exhibit heavy right tails (skewness and excess kurtosis) due to packet retransmissions, route flapping, and exchange engine queuing.

### Empirical CDF & Percentile Milestones
For any benchmark dataset of measured delays $\{\tau_i\}_{i=1}^M$:
- **$p_{50}$ (Median / Normal Operation):** Expected baseline latency under calm market conditions.
- **$p_{90}$ (Mild Load):** Routine network fluctuations and order book volatility.
- **$p_{99}$ (Engine Congestion):** Bursts of order submissions during fast market breakouts.
- **$p_{99.9}$ (Panic / Severe Queue Delay):** Circuit breakers, liquidation cascades, and exchange throttling.

---

## 5. Mandatory Backtest Latency Haircut Scenarios

Every candidate strategy evaluated on tick or order book data MUST be subjected to four discrete latency scenarios in `bithumb_coin_trader.execution_simulator`:

| Scenario ID | Latency Delay ($\tau$) | Target Condition | Required Validation Outcome |
| :--- | :--- | :--- | :--- |
| **SCENARIO-BASE** | **50 ms** | Ideal low-latency AWS Seoul connection | Must achieve target net Sharpe hurdle ($> 1.5$) |
| **SCENARIO-STRESS-1** | **100 ms** | Standard API routing under regular load | Net PnL must remain strictly positive |
| **SCENARIO-STRESS-2** | **250 ms** | Elevated queue times & burst volume | Net PnL must remain non-negative |
| **SCENARIO-PANIC** | **500 ms** | Severe market stress / exchange rate-limiting | Strategy must avoid catastrophic ruin (MDD $< 10\%$) |

Any strategy that only produces positive expectancy under 0 ms or $<50$ ms assumptions is **REJECTED** as a latency-fragile artifact.
