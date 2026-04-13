# PolyWeather — Code Explainer

## What This App Does

PolyWeather is an automated weather temperature prediction trading system for Polymarket. It:

1. **Scans** Polymarket weather markets (e.g., "Will NYC be above 20°C tomorrow?")
2. **Forecasts** using multi-model weather data (Open-Meteo ensemble) to estimate the real probability
3. **Identifies edges** where the market price differs from the forecast probability
4. **Trades automatically** — both paper trading (simulated) and live trading (real money on Polymarket)
5. **Manages portfolios** with independent strategies, capital limits, and risk controls
6. **Analyzes performance** with breakdowns, AI insights, and historical comparisons

There are two independent trading strategies:
- **Temperature Predictions** (original) — multi-model forecasts, edge detection, paper + live trading
- **Max Temp Sniper** (experimental) — real-time METAR-triggered, fires when observed temp exceeds band thresholds

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│              Vercel (Frontend + API)                  │
│  public/*.html          api/*.py                     │
│  (Dashboard, P&L,       (Scanner, trades,            │
│   Analysis, Exits)       portfolios, auth)            │
└──────────────┬───────────────────┬───────────────────┘
               │                   │
               ▼                   ▼
┌──────────────────┐    ┌──────────────────────────────┐
│    Supabase      │    │   Railway Trader Service     │
│  (Postgres DB)   │◄──►│   railway-trader/            │
│                  │    │  • Flask REST API (app.py)    │
│  Tables:         │    │  • Trading Loop (daemon)     │
│  • portfolios    │    │  • Wallet Manager            │
│  • paper_trades  │    │  • CLOB client               │
│  • opportunities │    └──────────┬───────────────────┘
│  • exit_snapshots│               │
│  • execution_log │               ▼
│  • ai_analyses   │    ┌──────────────────────────────┐
│  • wallets       │    │   Polymarket CLOB API        │
│  • scans         │    │   (Order execution,          │
└──────────────────┘    │    prices, balances)          │
                        └──────────────────────────────┘

Separate system (do not mix):
┌──────────────────────────────────────────────────────┐
│   Max Temp Sniper (max-temp-sniper/)                 │
│   Separate Railway service, separate DB tables       │
│   Polls METAR every 10s, fires on temp triggers      │
└──────────────────────────────────────────────────────┘
```

---

## File Structure & Responsibilities

### Backend — Root Python Files (Vercel Serverless Helpers)

| File | Purpose |
|------|---------|
| `scanner.py` | Market scanning logic — fetches Polymarket weather markets, extracts temperature bands, identifies tradeable opportunities |
| `stats_agent.py` | Trade signal generation — multi-model weather forecasts, Kelly sizing, confidence scoring, edge calculation |
| `paper_trading.py` | Paper trade management — opens trades (with capital checks), resolves via Polymarket market outcomes, calculates P&L |
| `live_trading.py` | Live trade execution — same filters as paper, but calls Railway `/execute` to place real orders. Passes `wallet_address` per portfolio |
| `polymarket_api.py` | Polymarket API client — Gamma API (market data) + CLOB API (order book, prices) |
| `config.py` | Trading thresholds, city tiers, API configuration constants |

### Backend — API Endpoints (`api/`)

**Core Trading:**

| File | Endpoint | Purpose |
|------|----------|---------|
| `scan.py` | `/api/scan` | Cron-triggered scanner — finds opportunities, routes to paper or live trading per portfolio. Modes: `tomorrow`, `all`, `custom` |
| `resolve.py` | `/api/resolve` | Cron (every 30 min) — resolves open trades by checking Polymarket market outcomes |
| `trades.py` | `/api/trades` | Trade listing, summaries, capital info. Supports `?portfolio_id=`, `?capital=true`, `?status=` |
| `trades_all.py` | `/api/trades_all` | Cross-portfolio trade listing with portfolio names |
| `execution_log.py` | `/api/execution_log` | Query execution log entries — trade attempts, errors, capital snapshots |

**Portfolio & Wallet Management:**

| File | Endpoint | Purpose |
|------|----------|---------|
| `portfolios.py` | `/api/portfolios` | CRUD for portfolios — strategy JSON, capital settings, trade_mode (paper/live), wallet_address |
| `wallets.py` | `/api/wallets` | Wallet metadata in Supabase (label, address, active) — no private keys stored |
| `wallet.py` | `/api/wallet` | Proxy to Railway `/balance` for wallet balance checks |
| `wallet_proxy.py` | `/api/wallet_proxy` | Server-side proxy to Railway for wallet registration and balance — keeps Railway API secret on server |

**Analysis & Exits:**

| File | Endpoint | Purpose |
|------|----------|---------|
| `analyze.py` | `/api/analyze` | Claude AI analysis — sends trade data + breakdowns to Claude Sonnet, saves history |
| `dashboard_analysis.py` | `/api/dashboard_analysis` | Multi-portfolio AI summary for the compare/dashboard page |
| `forecast_compare.py` | `/api/forecast_compare` | Compares original forecast vs latest forecast for open trades — shows if thesis has changed |
| `refresh_prices.py` | `/api/refresh_prices` | Updates live market prices for open trades from Polymarket CLOB |
| `exit_snapshots.py` | `/api/exit_snapshots` | Query exit recommendation snapshots — historical exit-vs-hold analysis |

**Auth & Misc:**

| File | Endpoint | Purpose |
|------|----------|---------|
| `auth.py` | `/api/auth` | Authentication — Supabase OTP email login + TOTP MFA. Actions: send-code, verify, check, logout, enroll-totp, challenge-totp, verify-totp |
| `log.py` | `/api/log` | Query scan/trade logs |
| `history.py` | `/api/history` | Trade history queries |
| `notify.py` | `/api/notify` | Alert/notification system |

**Max Temp Sniper (separate strategy):**

| File | Endpoint | Purpose |
|------|----------|---------|
| `sniper_signals.py` | `/api/sniper_signals` | Real-time max temp trigger signals |
| `sniper_trades.py` | `/api/sniper_trades` | Sniper trade history |
| `sniper_pnl.py` | `/api/sniper_pnl` | Sniper-specific P&L |
| `sniper_resolve.py` | `/api/sniper_resolve` | Resolve sniper trade outcomes (cron every 30 min) |
| `sniper_speed.py` | `/api/sniper_speed` | Execution speed metrics |
| `sniper_potential.py` | `/api/sniper_potential` | Potential sniper signals |
| `metar_history.py` | `/api/metar_history` | METAR weather observation history |

### Frontend (`public/`)

**Temperature Predictions pages:**

| File | Route | Purpose |
|------|-------|---------|
| `compare.html` | `/` (home) | Dashboard — multi-portfolio comparison table, cumulative P&L chart, AI trade summary |
| `trades.html` | `/trades` | Trading dashboard — portfolio management, trade table, scanner controls, wallet setup wizard, strategy editor |
| `pnl.html` | `/pnl` | P&L — cumulative/daily charts, capital status bar, trade history with infinite scroll, CSV export |
| `analysis.html` | `/analysis` | Results analysis — 18 breakdown dimensions, cross-analysis, charts, binomial significance, AI insights |
| `exits.html` | `/exits` | Smart Exits — open positions with exit recommendations, forecast gap, captured %, available opportunities |
| `login.html` | `/login` | Authentication — email OTP, 2FA setup with QR code, session management |
| `strategy-editor.js` | (module) | Reusable strategy editing form — all strategy JSON fields rendered as an editable UI |

**Max Temp Sniper pages (separate section):**

| File | Route | Purpose |
|------|-------|---------|
| `sniper.html` | `/sniper` | Sniper dashboard — real-time signals, METAR data, speed report |
| `sniper-trades.html` | `/sniper/trades` | Sniper trade history |
| `sniper-history.html` | `/sniper/history` | METAR temperature history by station |
| `sniper-settings.html` | `/sniper/settings` | Sniper configuration |

### Railway Trader Service (`railway-trader/`)

| File | Purpose |
|------|---------|
| `app.py` | Flask REST API — `/execute` (place orders), `/balance`, `/wallets/*` (multi-wallet management), `/set-allowances`, `/swap` (USDC↔USDC.e), `/redeem` (claim winnings), `/loop-control` |
| `trading_loop.py` | Background daemon thread — polls prices every 3s, evaluates portfolios every 60s, auto-redeems every 30min, collects exit snapshots every 30min |
| `wallet_manager.py` | Multi-wallet storage — loads from env vars + JSON file, initializes CLOB clients, never exposes private keys |
| `requirements.txt` | flask, gunicorn, py-clob-client, web3 |
| `railway.toml` | Deploy config — single gunicorn worker, /health check, restart on failure |

### Max Temp Sniper (`max-temp-sniper/`)

| File | Purpose |
|------|---------|
| `main.py` | Entry point — coordinates three async loops (market refresh, METAR poll, heartbeat) |
| `market_scanner.py` | Fetches Polymarket weather markets, extracts temperature bands |
| `metar_poller.py` | Polls METAR data every 10s for all active stations |
| `signal_engine.py` | Detects when observed temp crosses band thresholds |
| `order_executor.py` | Executes trades on Polymarket CLOB |
| `position_tracker.py` | Tracks open positions |
| `price_tracker.py` | Monitors price reactions post-trigger |
| `risk_manager.py` | Position sizing and capital constraints |

### Config Files

| File | Purpose |
|------|---------|
| `vercel.json` | Crons (scanner every 5-10min, resolver every 30min), URL rewrites, function memory/timeout settings |
| `.env.local` | Local env vars — Supabase URL/keys, Open-Meteo key (gitignored) |
| `.claude/settings.local.json` | Claude Code hooks — auto-commit on edit, Vercel deploy status after push |
| `railway-trader/railway.toml` | Railway deploy — nixpacks build, gunicorn start, health check |

---

## Database Tables (Supabase)

**Temperature Predictions:**

| Table | Purpose |
|-------|---------|
| `portfolios` | Portfolio definitions — name, strategy (JSONB), capital, trade_mode (paper/live), wallet_address |
| `paper_trades` | All trades (paper + live) — status (open/pending_execution/won/lost/exited), entry/exit prices, P&L, execution details |
| `opportunities` | Scanner-found opportunities per scan |
| `scans` | Scan metadata (mode, timestamp) |
| `exit_snapshots` | Smart exit recommendations — hypothetical exit P&L, forecast gap, captured %, backfilled with actual outcomes |
| `execution_log` | Detailed execution traces — trade attempts, errors, capital snapshots, timing |
| `ai_analyses` | Saved Claude AI analyses with portfolio and date range |
| `wallets` | Wallet metadata (label, address, active) — NO private keys |

**Max Temp Sniper:**

| Table | Purpose |
|-------|---------|
| `sniper_trades` | Sniper trade records |
| `sniper_signals` | Temperature trigger signals |

---

## Key Data Flows

### 1. Scanner → Trade Execution
```
Vercel Cron (every 5-10 min)
  → /api/scan
    → scanner.py finds markets
    → stats_agent.py forecasts + edges
    → For each portfolio:
      → paper_trading.py (paper mode)
      → live_trading.py → Railway /execute → Polymarket CLOB (live mode)
```

### 2. Railway Trading Loop (live portfolios only)
```
Every 60s: fetch portfolios + opportunities from Supabase
Every 3s: poll Polymarket midpoint prices
  → Evaluate edge for each portfolio × opportunity
  → Check strategy filters, capital limits, dup filter
  → Execute via CLOB client → update trade in Supabase
Every 30 min: auto-redeem won positions, collect exit snapshots
```

### 3. Trade Resolution
```
Vercel Cron (every 30 min)
  → /api/resolve
    → Check Polymarket Gamma API for market outcomes
    → Update trade status: open → won/lost
    → Calculate profit_usd, payout_usd, roi_pct
    → Backfill exit_snapshots with actual_outcome
```

### 4. Smart Exits (analysis only, no auto-execution yet)
```
Railway loop (every 30 min)
  → Fetch open live trades
  → Compare current price + latest forecast vs band threshold
  → Generate recommendation (hold/take_profit/danger/exit_forecast_changed)
  → INSERT snapshot to exit_snapshots table
  → exits.html displays recommendations with captured % and gap
```

---

## Cron Schedule

| Schedule | Endpoint | Purpose |
|----------|----------|---------|
| Every 10 min (0,10,20...) | `/api/scan?mode=tomorrow` | Scan tomorrow's markets |
| Every 10 min (5,15,25...) | `/api/scan?mode=all` | Scan all available markets |
| Every 30 min | `/api/resolve` | Resolve temp prediction trades |
| Every 30 min | `/api/sniper_resolve` | Resolve sniper trades |

---

## Portfolio Strategy Schema (JSONB)

```json
{
  "safe_no": { "min_prob", "min_return", "max_no_price", "min_no_price", "min_confidence" },
  "edge_bet": { "min_edge", "min_prob", "max_price", "min_confidence", "max_confidence", "max_edge" },
  "allowed_sides": ["NO"],
  "allowed_bet_types": ["safe_no", "edge"],
  "allowed_band_types": ["exact", "above", "below"],
  "allowed_cities": [],
  "blocked_cities": ["Singapore", "Seoul"],
  "position_sizing": { "kelly_fraction", "min_liquidity_usd", "min_edge_after_slippage" },
  "capital_management": {
    "max_single_trade_pct": 5,
    "max_single_trade_usd": 250,
    "max_portfolio_utilization_pct": 98,
    "max_correlated_exposure_pct": 25
  },
  "capital_allocation": { "sort_field": "ev_per_dollar", "sort_weights": { "edge", "confidence", "ev_per_dollar" } },
  "trading_hours": { "enabled": false, "allowed_windows": [], "blackout_windows": [] },
  "ensemble_std_min": null,
  "ensemble_std_max": null,
  "preferred_entry_price_min": null
}
```
