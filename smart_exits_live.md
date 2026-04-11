# Smart Exits Live A/B Test — Research & Implementation Plan

**Date:** April 11, 2026
**Purpose:** Test whether auto-selling positions early (capital recycling) beats holding to resolution
**Strategy:** Safe NO Volume (100% WR — exits are about freeing capital, not avoiding losses)
**Capital:** $100 per portfolio ($200 total)

---

## Current Smart Exits System State

### What EXISTS and is running:
- **Exit recommendation engine** — runs every 30 min in Railway trading loop
- **Exit snapshots table** — stores recommendations with hypothetical P&L
- **Exits UI** (`exits.html`) — shows open positions with recommendations
- **`/execute` endpoint** — already supports SELL orders
- **Auto-redeem** — redeems won positions every 30 min

### What DOESN'T exist yet:
- Auto-execution of sell orders based on recommendations
- `smart_exits` config section in portfolio strategy JSON
- "exited" trade status
- Manual "Sell Now" button in UI

---

## Exit Recommendation Algorithm

**File:** `railway-trader/trading_loop.py` — `_get_exit_recommendation()`

Evaluates each open trade based on two metrics:
1. **Forecast gap** — distance between latest forecast and band threshold (in °C)
2. **Captured upside %** — how much of max possible profit is already realized

**Recommendations (priority order):**

| Recommendation | Trigger | Action |
|---|---|---|
| `take_profit` | captured >= 90% | Sell — almost all profit captured |
| `exit_forecast_changed` | gap < 0 (forecast crossed band) | Sell — thesis broken |
| `danger` | gap < 1°C AND captured < 50% | Sell — high risk, low reward |
| `consider_exit` | captured 50-80% with 3°C+ gap | Optional — profitable but thesis intact |
| `hold` | gap >= 3°C OR captured < 50% with safe gap | Hold — thesis intact |

---

## Architecture for Live Exit Execution

### How the trading loop works (Railway service):
- `TradingLoop` class runs as a daemon thread alongside Flask app
- Polls prices every 3s, refreshes portfolios/opportunities every 60s
- Every 30 min: collects exit snapshots via `_collect_exit_snapshots()`
- Does NOT have direct ClobClient access — must call `/execute` via HTTP (same pattern as `_auto_redeem` at line 752)

### Exit execution flow:
```
_collect_exit_snapshots()
  → for each open live trade:
    → _get_exit_recommendation(trade, live_price, forecast)
    → if recommendation != "hold" AND portfolio.smart_exits.enabled:
      → _execute_exit(trade, exit_price, recommendation, portfolio)
        → POST http://localhost:PORT/execute {side: "SELL", token_id, size, price, wallet_address}
        → if filled: update trade status to "exited", set profit_usd
        → if not filled: log and retry next cycle
```

### Key integration points:
- **Line 844:** recommendation generated — add exit check after this
- **Line 847:** `if recommendation == "hold": continue` — execution goes after this check
- **Line 752:** `_auto_redeem` — pattern to follow for calling `/execute` via localhost HTTP

---

## Strategy Config (JSONB — no schema migration needed)

Portfolio A gets:
```json
"smart_exits": {
  "enabled": true,
  "auto_take_profit_pct": 90,
  "auto_exit_on_danger": true,
  "auto_exit_on_forecast_change": true
}
```

Portfolio B gets:
```json
"smart_exits": {
  "enabled": false
}
```

---

## New Trade Status: "exited"

Current statuses: `open`, `pending_execution`, `won`, `lost`
New: `exited` — sold early via smart exits

### Impact on existing queries:

| File | Current Query | Change Needed |
|------|---|---|
| `paper_trading.py` resolve | `status=eq.open` | None (exited trades excluded automatically) |
| `api/trades.py` closed trades | `status=in.(won,lost)` | Add `exited` |
| `api/trades_all.py` | `status=in.(won,lost)` | Add `exited` |
| P&L calculations | sum profit_usd where won/lost | Add `exited` |
| `exits.html` UI | shows `status=eq.open` | Exited trades disappear naturally |

### What gets stored on exit:
- `status = "exited"`
- `profit_usd = actual_received - total_cost_usd`
- `resolved_at = now()`
- `execution_details = {exit_type, exit_price, exit_reason, exit_order_id}`

---

## Comparison Framework

### Direct comparison:
- Portfolio A total P&L vs Portfolio B total P&L over same period
- Portfolio A includes capital recycling benefit (more trades taken with freed capital)

### Per-trade comparison (via exit_snapshots table):
- `exit_vs_hold` = hypothetical exit profit - actual hold profit
- Already backfilled when markets resolve
- Shows whether each individual exit was the right call

### What we expect to see:
- Portfolio A: slightly lower profit per trade (selling at $0.99 vs $1.00 resolution)
- Portfolio A: MORE trades taken (capital freed faster)
- Net effect: Portfolio A should have higher total P&L if capital is the bottleneck

---

## Files to Modify

| File | Change | Side |
|------|--------|------|
| `railway-trader/trading_loop.py` | Add `_execute_exit()` method + call from `_collect_exit_snapshots()` | Temp Predictions |
| `api/trades.py` | Include "exited" in closed-trade queries | Temp Predictions |
| `api/trades_all.py` | Include "exited" in closed-trade queries | Temp Predictions |

### Files NOT touched (Max Temp Sniper):
- `max-temp-sniper/*`
- `api/sniper_*.py`
- `public/sniper*.html`

---

## Order Type for Exits

**FOK (Fill-or-Kill)** recommended over GTC:
- We want immediate execution, not a hanging order
- Safe NO positions at 90%+ captured are priced near $0.95-0.99 — good liquidity
- If FOK fails (no buyers), no harm — trade stays open, retries in 30 min
- GTC risk: order sits on book, price moves, partial fills complicate tracking

---

## Test Portfolios

| | Portfolio A | Portfolio B |
|---|---|---|
| Name | Smart Exit Test (ON) | Smart Exit Test (OFF) |
| trade_mode | live | live |
| starting_capital | $100 | $100 |
| wallet_address | new wallet | new wallet (same) |
| Strategy base | Safe NO Volume clone | Safe NO Volume clone |
| smart_exits.enabled | true | false |

Both portfolios share the same wallet — `paper_trades` table tracks ownership.

---

## Verification Checklist

- [ ] Deploy Railway with exit execution code
- [ ] Check `/health` and `/loop-status`
- [ ] Both portfolios appear in trading loop logs
- [ ] First trades placed by both portfolios
- [ ] First exit triggers on Portfolio A — verify status="exited", profit set
- [ ] Portfolio A's freed capital used for new trades
- [ ] Compare page shows both portfolios side by side
- [ ] After a few days: compare total P&L
