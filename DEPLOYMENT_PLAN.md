# PolyWeather — Vercel + Supabase + Auto-Trading Deployment Plan

## Architecture

```
Vercel (Next.js)                    Supabase (Postgres)
┌─────────────────────┐             ┌──────────────────────┐
│  Frontend (React)   │             │  scans               │
│  - Dashboard UI     │◄──────────► │  opportunities       │
│  - Trade history    │             │  trades              │
│  - P&L tracker      │             │  outcomes            │
│                     │             │  forecast_accuracy   │
│  API Routes         │             │  wallet_config       │
│  - /api/scan        │             └──────────────────────┘
│  - /api/execute     │
│  - /api/trades      │             Polymarket
│  - /api/balance     │             ┌──────────────────────┐
│                     │────────────►│  CLOB API (orders)   │
│  Cron Job           │             │  Gamma API (events)  │
│  - Hourly scan      │             └──────────────────────┘
│  - Auto-execute     │
└─────────────────────┘             Open-Meteo
                                    ┌──────────────────────┐
                                    │  Forecast API        │
                                    │  Ensemble API        │
                                    └──────────────────────┘
```

## Tech Stack
- **Frontend**: Next.js 14 (App Router) on Vercel
- **Backend**: Python serverless functions on Vercel (`/api/*.py`)
- **Database**: Supabase (Postgres + Row Level Security)
- **Trading**: `py-clob-client` (Polymarket's official Python SDK)
- **Cron**: Vercel Cron Jobs
- **Wallet**: Polygon wallet with USDC.e, private key in Vercel env vars

## Why Python on Vercel
- Vercel supports Python serverless functions natively
- Keeps the existing stats engine (scipy, numpy) as-is
- `py-clob-client` is Python — native integration for trading
- Tradeoff: 50MB max bundle, cold starts ~2-3s

## Project Structure

```
polyweather/
├── app/                          # Next.js App Router
│   ├── page.tsx                  # Dashboard (port existing index.html)
│   ├── layout.tsx
│   ├── trades/page.tsx           # Trade history view
│   └── analytics/page.tsx        # P&L and accuracy analytics
├── api/                          # Vercel Python serverless functions
│   ├── scan.py                   # Run scanner, save to Supabase
│   ├── execute.py                # Execute a trade on Polymarket
│   ├── auto_execute.py           # Auto-execute qualifying trades
│   ├── trades.py                 # Get trade history from Supabase
│   ├── balance.py                # Check wallet USDC balance
│   ├── outcomes.py               # Check/resolve trade outcomes
│   └── cancel.py                 # Cancel open orders
├── lib/
│   ├── scanner/                  # Python scanner modules (existing)
│   │   ├── config.py
│   │   ├── weather_api.py
│   │   ├── polymarket_api.py
│   │   ├── stats_agent.py
│   │   └── trader.py             # NEW: py-clob-client wrapper
│   └── supabase.ts               # Supabase client for frontend
├── components/                   # React components
│   ├── TradeCard.tsx
│   ├── SizeLadder.tsx
│   ├── TradeHistory.tsx
│   ├── WalletStatus.tsx
│   └── AutoTradeSettings.tsx
├── supabase/
│   └── migrations/
│       └── 001_initial.sql
├── vercel.json                   # Cron config
├── requirements.txt
├── package.json
└── .env.local                    # Secrets (not committed)
```

## Supabase Schema

```sql
-- Scan results
CREATE TABLE scans (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  timestamp TIMESTAMPTZ DEFAULT NOW(),
  mode TEXT,
  duration_seconds FLOAT,
  total_opportunities INT,
  sure_bets INT,
  edge_bets INT,
  safe_no_bets INT
);

-- Individual opportunities from each scan
CREATE TABLE opportunities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_id UUID REFERENCES scans(id),
  city TEXT,
  date TEXT,
  side TEXT,
  bet_type TEXT,
  band_c TEXT,
  band_type TEXT,
  forecast_c FLOAT,
  my_p FLOAT,
  mkt_p FLOAT,
  edge FLOAT,
  confidence INT,
  ev_per_dollar FLOAT,
  hk FLOAT,
  risk TEXT,
  question TEXT,
  token_id TEXT,
  condition_id TEXT,
  event_slug TEXT,
  url TEXT,
  liquidity JSONB,
  model_values JSONB
);

-- Executed trades
CREATE TABLE trades (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id UUID REFERENCES opportunities(id),
  timestamp TIMESTAMPTZ DEFAULT NOW(),
  city TEXT,
  side TEXT,
  token_id TEXT,
  price FLOAT,
  size FLOAT,
  bet_usd FLOAT,
  order_id TEXT,
  status TEXT,  -- posted/filled/cancelled/failed
  edge_pp FLOAT,
  confidence INT,
  question TEXT
);

-- Trade outcomes (resolved)
CREATE TABLE outcomes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trade_id UUID REFERENCES trades(id),
  resolved_at TIMESTAMPTZ,
  actual_temp_c FLOAT,
  won BOOLEAN,
  payout_usd FLOAT,
  profit_usd FLOAT
);

-- Forecast accuracy tracking
CREATE TABLE forecast_accuracy (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  city TEXT,
  date TEXT,
  forecast_c FLOAT,
  actual_c FLOAT,
  error_c FLOAT,
  model_values JSONB,
  recorded_at TIMESTAMPTZ DEFAULT NOW()
);

-- Wallet config / auto-trade settings
CREATE TABLE wallet_config (
  id INT PRIMARY KEY DEFAULT 1,
  auto_trade_enabled BOOLEAN DEFAULT FALSE,
  min_confidence INT DEFAULT 75,
  min_edge_pp FLOAT DEFAULT 10,
  max_bet_usd FLOAT DEFAULT 25,
  max_daily_usd FLOAT DEFAULT 100,
  allowed_bet_types TEXT[] DEFAULT '{edge,safe_no}'
);
```

## Vercel Cron Jobs

```json
// vercel.json
{
  "crons": [
    {
      "path": "/api/scan?mode=tomorrow&auto_execute=true",
      "schedule": "0 */4 * * *"
    },
    {
      "path": "/api/outcomes",
      "schedule": "0 6 * * *"
    }
  ]
}
```

- Scanner runs every 4 hours, auto-executes qualifying trades
- Outcomes checker runs daily at 6am to resolve yesterday's bets

## Environment Variables (Vercel Dashboard)

```
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_KEY=eyJ...
POLYMARKET_PRIVATE_KEY=0x...
POLYMARKET_FUNDER_ADDRESS=0x...
OPEN_METEO_API_KEY=...  (when you get one)
```

## Trading Setup (py-clob-client)

```python
# trader.py — wraps Polymarket's official SDK
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

client = ClobClient(
    host="https://clob.polymarket.com",
    key=POLYMARKET_PRIVATE_KEY,
    chain_id=137,  # Polygon
    signature_type=0,  # EOA (MetaMask/hardware wallet)
    funder=POLYMARKET_FUNDER_ADDRESS
)
client.set_api_creds(client.create_or_derive_api_creds())

# Place order
order = OrderArgs(token_id="...", price=0.06, size=83, side=BUY)
signed = client.create_order(order)
result = client.post_order(signed, OrderType.GTC)
```

## Wallet Requirements
- Polygon wallet with USDC.e (`0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`)
- POL for gas fees (if using EOA signature type)
- Token approvals set for CTF Exchange (`0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E`)

## Safety Measures
- Auto-trading OFF by default (wallet_config table)
- Max single bet: $25 (configurable)
- Max daily spend: $100 (configurable)
- Min confidence: 75, min edge: 10pp
- All trades logged to Supabase with full context
- Cancel endpoint for emergency stop
- Outcomes tracker verifies actual results vs predictions

## Implementation Order
1. Set up Supabase project + create tables
2. Set up Next.js project on Vercel
3. Port Python scanner to Vercel serverless functions
4. Create trader.py with py-clob-client
5. Build API routes (scan, execute, trades, balance, outcomes)
6. Port frontend to React components
7. Add trade history + P&L analytics pages
8. Configure Vercel cron jobs
9. Deploy and test with small bets ($5)
10. Gradually increase limits as confidence builds

## TODO Before Starting
- [ ] Get Open-Meteo API key (for full 122-member ensemble)
- [ ] Export Polygon wallet private key
- [ ] Set token approvals on Polygon (USDC + CTF)
- [ ] Create Supabase project
- [ ] Create Vercel project linked to this repo
