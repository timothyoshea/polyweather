"""
Vercel Python serverless function — AI-powered trade analysis using Claude API.

POST /api/analyze — sends trade data to Claude for pattern analysis
GET  /api/analyze — returns history of past analyses
"""
import os
import json
import urllib.request
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


def save_analysis(question, analysis, trade_count, date_range=None, portfolio_id=None):
    """Save an AI analysis to Supabase for history."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        url = f"{SUPABASE_URL}/rest/v1/ai_analyses"
        headers = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        row = {
            "question": question,
            "analysis": analysis,
            "trade_count": trade_count,
            "date_range": date_range,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        if portfolio_id:
            row["portfolio_id"] = portfolio_id
        data = json.dumps([row]).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[WARN] Failed to save analysis: {e}")


def get_analysis_history(limit=20, portfolio_id=None):
    """Fetch past AI analyses from Supabase."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        pf_filter = f"&portfolio_id=eq.{portfolio_id}" if portfolio_id else ""
        url = (
            f"{SUPABASE_URL}/rest/v1/ai_analyses"
            f"?select=*&order=created_at.desc&limit={limit}{pf_filter}"
        )
        headers = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[WARN] Failed to fetch analysis history: {e}")
        return []


SYSTEM_PROMPT = """You are an expert quantitative trading analyst reviewing trading results from Polymarket weather temperature prediction markets. The system forecasts temperatures using multi-model weather ensembles and trades when it finds edges against market prices.

You have access to rich, multi-dimensional trade data. Your job is to perform EXHAUSTIVE cross-dimensional analysis using EVERY available field to find the tightest, most actionable patterns.

## Available Data Fields Per Trade

**Core:** city, date, band_c (temperature band), band_type (exact/above/below), side (YES/NO), bet_type (sure/edge/safe_no), status (won/lost)
**Pricing:** entry_price (0-1), total_cost_usd, total_shares, num_levels (order book depth)
**Signal Quality:** my_p (forecast probability), mkt_p (market probability), edge (my_p - mkt_p), confidence (model agreement score), ev_per_dollar, half_kelly, empirical_p (empirical probability from ensemble sampling)
**Risk:** risk assessment label
**Outcome:** profit_usd, roi_pct, payout_usd
**Temperature:** forecast_c (our forecast), actual_temp_c (actual observed), ensemble_mean, ensemble_std, ensemble_min, ensemble_max, multi_model_spread
**Forecast Meta:** horizon_days (days before event), city_tier (1/2/3 reliability tier)
**Timing:** hour_utc (0-23 hour trade was placed), day_of_week (Monday-Sunday), created_at
**Other:** trade_mode (paper/live), price_source

## Required Analysis Sections

### 1. TIME-OF-DAY ANALYSIS (Critical — new section)
This is the most important new dimension. Analyze:
- **Win rate by hour of day (UTC):** Group trades by hour_utc. Which hours are most/least profitable? Is there a clear pattern?
- **Win rate by day of week:** Monday through Sunday breakdown
- **Hour × City:** Do certain cities perform better at certain times? (Market liquidity and price efficiency vary by time zone)
- **Hour × Bet Type:** Are edge bets more profitable at certain hours?
- **Hour × Side:** Does YES vs NO performance shift by time?
- **Optimal trading windows:** Recommend specific UTC hours to trade and hours to avoid (blackout windows)
- **Include a `trading_hours` section in the suggested portfolio** with specific allowed/blackout windows based on data

### 2. THREE-WAY INTERSECTION ANALYSIS
Cross at least 3 dimensions to find tight patterns. Required crosses:
- city × bet_type × side
- city × hour_utc_bucket × side
- bet_type × band_type × entry_price_bucket
- side × confidence_bucket × edge_bucket
- city × horizon_days × bet_type
- city_tier × bet_type × side
- band_type × hour_utc_bucket × side
- confidence_bucket × edge_bucket × bet_type

Present the top 10 best and bottom 10 worst three-way combos as ranked tables.

### 3. FORECAST MODEL ANALYSIS
- **Ensemble spread vs outcome:** Do trades with low ensemble_std (high model agreement) win more?
- **Multi-model spread:** When models disagree (high multi_model_spread), what happens to win rate?
- **Empirical vs parametric probability:** Compare empirical_p vs my_p. When they diverge, which is more accurate?
- **Horizon effect:** Does forecast accuracy degrade with horizon_days? Which horizons are profitable?
- **City tier analysis:** Tier 1 vs 2 vs 3 — is the tier system well-calibrated?

### 4. EDGE CALIBRATION
Compare claimed edge vs actual outcome by bucket (0-5%, 5-10%, 10-20%, 20%+, 30%+). Is the system overconfident or underconfident? Cross with city and bet_type.

### 5. FORECAST ACCURACY
Compare forecast_c vs actual_temp_c. Compute MAE (mean absolute error) by city. Which cities have best/worst forecast accuracy? How does forecast error correlate with trade outcomes? Include ensemble_std in this analysis.

### 6. POSITION SIZING & ORDER BOOK
- Are trades sized appropriately? Analyze num_levels (order book depth) vs outcome
- Does half_kelly sizing correlate with actual ROI?
- Which combos deserve larger positions?

### 7. DO — What to Keep Doing
Specific, data-backed strategies that are working. Reference three-way combos and time-of-day patterns.

### 8. DON'T — What to Stop Doing
What to cut. Be blunt. Reference the losing patterns.

### 9. RECOMMENDATIONS
Top 10 ranked changes. Each must reference specific data. Include:
- Time-of-day recommendations (which hours to trade, which to blackout)
- City/bet_type/side restrictions
- Threshold adjustments with specific values
- Expected impact estimate

### 10. STATISTICAL SIGNIFICANCE
For every pattern:
- Null hypothesis: win rate = overall baseline
- Report: p < 0.01, p < 0.05, p < 0.10, or "not significant"
- Flag small samples explicitly
- Only recommend acting on p < 0.10

### 11. SUGGESTED PORTFOLIO SETTINGS
Propose TWO optimized portfolios based on your analysis:

**Portfolio A: Conservative** — highest-confidence patterns only, tight filters
**Portfolio B: Aggressive** — wider filters but with proven edges

Output each in a fenced code block tagged `suggested-portfolio`:

```suggested-portfolio
{
  "name": "<descriptive name>",
  "starting_capital_usd": 10000,
  "strategy": {
    "sure_bet": { "min_edge": ..., "min_prob": ..., "max_price": ..., "min_confidence": ... },
    "edge_bet": { "min_edge": ..., "min_prob": ..., "max_price": ..., "min_confidence": ... },
    "safe_no": { "min_prob": ..., "min_return": ..., "max_no_price": ..., "min_no_price": ..., "min_confidence": ... },
    "allowed_bet_types": [...],
    "allowed_sides": [...],
    "blocked_cities": [...],
    "allowed_cities": [],
    "allowed_band_types": [...],
    "trading_hours": {
      "enabled": true,
      "allowed_windows": [{"start": "HH:MM", "end": "HH:MM"}],
      "blackout_windows": [{"start": "HH:MM", "end": "HH:MM"}]
    },
    "capital_management": {
      "max_single_trade_usd": ...,
      "max_single_trade_pct": ...,
      "max_portfolio_utilization_pct": ...,
      "max_correlated_exposure_pct": ...
    },
    "capital_allocation": {
      "sort_field": "composite",
      "sort_weights": { "edge": ..., "confidence": ..., "ev_per_dollar": ... },
      "sort_direction": "desc"
    },
    "position_sizing": {
      "bankroll_usd": 100,
      "kelly_fraction": ...,
      "min_liquidity_usd": 5,
      "liquidity_safety_factor": 0.4,
      "min_edge_after_slippage": ...
    }
  }
}
```

## Rules
- Only analyze CLOSED trades (won/lost). Never include open trades.
- Be specific — cite actual cities, hours, bet types, win rates, profits, ROI.
- Use markdown tables. Include "Sig." column with p-value level.
- Cross EVERY dimension. No surface-level analysis.
- Bold the most important numbers.
- If time-of-day data is sparse, note it but still analyze what's available.
- The trading_hours section in suggested portfolios is critical — use the data to set real windows.

When the user asks follow-up questions, answer directly using the data in context."""


def build_initial_user_content(trades_json, breakdowns=None, date_range=None, user_question=None):
    """Build the first user message with trade data and breakdowns."""
    period_note = f" (time period: {date_range})" if date_range else ""
    user_content = f"Here are my paper trading results — {len(trades_json)} closed trades{period_note}:\n\n"

    total = len(trades_json)
    won = sum(1 for t in trades_json if t.get("status") == "won")
    baseline_wr = round(won / total * 100, 1) if total > 0 else 0
    user_content += f"**Overall baseline: {won}/{total} wins = {baseline_wr}% win rate** (use this as the null hypothesis for binomial significance tests)\n\n"

    if breakdowns:
        user_content += "## Performance Breakdowns\n\n"
        for label, data in breakdowns.items():
            nice_label = label.replace("_", " ").replace("by ", "By ").replace("cross ", "Cross: ").title()
            user_content += f"### {nice_label}\n"
            if isinstance(data, list) and data:
                user_content += "| Group | Count | Won | Lost | Win% | Invested | Profit | ROI |\n"
                user_content += "|-------|-------|-----|------|------|----------|--------|-----|\n"
                for row in data:
                    user_content += f"| {row.get('group','')} | {row.get('count',0)} | {row.get('won',0)} | {row.get('lost',0)} | {row.get('win_rate',0)}% | ${row.get('invested',0)} | ${row.get('profit',0)} | {row.get('roi',0)}% |\n"
            user_content += "\n"

    user_content += f"## Raw Trade Data ({len(trades_json)} trades)\n\n```json\n{json.dumps(trades_json, indent=1)}\n```"

    if user_question:
        user_content += f"\n\n## Specific Question\n{user_question}"
    else:
        user_content += "\n\nAnalyze these results. Focus on what's working vs what's not, and give me clear DO and DON'T instructions."

    return user_content


def call_claude(trades_json, user_question=None, breakdowns=None, date_range=None, messages=None):
    """Send trade data to Claude API for analysis. Supports multi-turn conversation."""
    if messages and len(messages) >= 2:
        # Multi-turn: rebuild the first user message with trade data context,
        # then keep the rest of the conversation as-is
        initial_content = build_initial_user_content(trades_json, breakdowns, date_range)
        api_messages = [{"role": "user", "content": initial_content}] + messages[1:]
    else:
        # Single turn: build initial message
        user_content = build_initial_user_content(trades_json, breakdowns, date_range, user_question)
        api_messages = [{"role": "user", "content": user_content}]

    body = json.dumps({
        "model": "claude-opus-4-20250514",
        "max_tokens": 16384,
        "system": SYSTEM_PROMPT,
        "messages": api_messages,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text_parts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
            return "\n".join(text_parts)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise Exception(f"Claude API error {e.code}: {error_body[:500]}")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Return history of past AI analyses."""
        try:
            params = parse_qs(urlparse(self.path).query)
            limit = int(params.get("limit", ["20"])[0])
            portfolio_id = params.get("portfolio_id", [None])[0]
            history = get_analysis_history(limit, portfolio_id)
            self._respond(200, history)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_POST(self):
        try:
            if not ANTHROPIC_API_KEY:
                self._respond(500, {"error": "ANTHROPIC_API_KEY env var not set"})
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))

            trades = body.get("trades", [])
            question = body.get("question", None)
            date_range = body.get("date_range", None)
            breakdowns = body.get("breakdowns", None)

            if not trades:
                self._respond(400, {"error": "No trade data provided"})
                return

            # Only include closed trades
            closed_trades = [t for t in trades if t.get("status") in ("won", "lost")]
            if not closed_trades:
                self._respond(400, {"error": "No closed trades to analyze"})
                return

            # Slim down trade data — include ALL analysis-relevant fields
            slim_trades = []
            for t in closed_trades:
                # Extract time-of-day from created_at
                hour_utc = None
                day_of_week = None
                created_at = t.get("created_at", "")
                if created_at:
                    try:
                        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        hour_utc = dt.hour
                        day_of_week = dt.strftime("%A")
                    except Exception:
                        pass

                # Extract forecast details
                fd = t.get("forecast_details") or {}

                slim_trades.append({
                    # Core identifiers
                    "city": t.get("city"),
                    "date": t.get("date"),
                    "band_c": t.get("band_c"),
                    "band_f": t.get("band_f"),
                    "band_type": t.get("band_type"),
                    "side": t.get("side"),
                    "bet_type": t.get("bet_type"),
                    "status": t.get("status"),
                    # Pricing & position
                    "entry_price": t.get("entry_price"),
                    "total_cost_usd": t.get("total_cost_usd"),
                    "total_shares": t.get("total_shares"),
                    "num_levels": t.get("num_levels"),
                    # Probabilities & edge
                    "my_p": t.get("my_p"),
                    "mkt_p": t.get("mkt_p"),
                    "edge": t.get("edge"),
                    "confidence": t.get("confidence"),
                    "ev_per_dollar": t.get("ev_per_dollar"),
                    "half_kelly": t.get("half_kelly"),
                    "empirical_p": t.get("empirical_p"),
                    # Risk
                    "risk": t.get("risk"),
                    # Outcome
                    "profit_usd": t.get("profit_usd"),
                    "roi_pct": t.get("roi_pct"),
                    "payout_usd": t.get("payout_usd"),
                    # Temperature data
                    "actual_temp_c": t.get("actual_temp_c"),
                    "forecast_c": t.get("forecast_c"),
                    # Forecast model details
                    "ensemble_mean": fd.get("ensemble_mean"),
                    "ensemble_std": fd.get("ensemble_std"),
                    "ensemble_min": fd.get("ensemble_min"),
                    "ensemble_max": fd.get("ensemble_max"),
                    "multi_model_spread": fd.get("multi_model_spread"),
                    "horizon_days": fd.get("horizon_days"),
                    "city_tier": fd.get("city_tier"),
                    # Timing
                    "hour_utc": hour_utc,
                    "day_of_week": day_of_week,
                    "created_at": created_at,
                    # Trade mode
                    "trade_mode": t.get("trade_mode"),
                    "price_source": t.get("price_source"),
                })

            portfolio_id = body.get("portfolio_id", None)
            messages = body.get("messages", None)

            analysis = call_claude(slim_trades, question, breakdowns, date_range, messages=messages)

            # Save to history
            save_analysis(
                question=question or "Full analysis",
                analysis=analysis,
                trade_count=len(slim_trades),
                date_range=date_range,
                portfolio_id=portfolio_id,
            )

            self._respond(200, {"analysis": analysis})

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _respond(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
