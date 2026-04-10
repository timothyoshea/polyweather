"""
Vercel Python serverless function — Claude Opus portfolio analysis for dashboard.

POST /api/dashboard_analysis  — run a new analysis (calls Claude Opus)
GET  /api/dashboard_analysis  — get latest/history of analyses
"""
import os
import json
import urllib.request
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _auth_helper import require_auth

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

SYSTEM_PROMPT = """You are a quantitative trading analyst reviewing a portfolio comparison dashboard for a weather temperature prediction trading system on Polymarket.

You are given summary metrics for every portfolio plus the raw trade breakdown (safe_no vs edge, by city). Your job is to provide a deep, actionable analysis.

Structure your response in markdown:

## Portfolio Rankings
Rank all portfolios by quality. For each, state: trades closed, win rate, ROI, reliability, and a one-line verdict.

## Key Patterns
What patterns emerge across the portfolios? Which strategies work, which don't? Be specific with numbers.

## Safe NO vs Edge Analysis
Compare safe_no and edge bet performance across all portfolios. Which combination works best?

## Live Portfolio Assessment
For any live portfolios, assess: are they performing as expected? Any concerns? Should strategy be adjusted?

## Risk Assessment
Which portfolios have concerning drawdowns, low sample sizes, or unreliable results?

## Top 3 Recommendations
Concrete, ranked actions to take right now. Reference specific portfolios and numbers.

## Suggested Portfolio Changes
For any underperforming portfolios, suggest specific parameter changes or whether to deactivate them.

Rules:
- Be blunt about what's losing money
- Every claim must reference a specific number
- Flag small sample sizes explicitly
- Bold the most important conclusions
- Use tables where helpful"""


def _supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _supabase_post(path, data):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_analysis_prompt():
    """Fetch all portfolio data and build a comprehensive prompt."""
    # Get all portfolios
    portfolios = _supabase_get("portfolios?select=id,name,description,trade_mode,active,starting_capital_usd,unlimited_capital,strategy&order=created_at.asc")

    # Get all closed trades with key fields
    trades = _supabase_get(
        "paper_trades?status=in.(won,lost)&select=portfolio_id,city,date,band_c,band_type,side,bet_type,status,"
        "entry_price,total_cost_usd,total_shares,my_p,mkt_p,edge,confidence,ev_per_dollar,profit_usd,roi_pct,"
        "risk,forecast_c,actual_temp_c,half_kelly&order=created_at.desc&limit=3000"
    )

    # Get open trade counts per portfolio
    open_trades = _supabase_get(
        "paper_trades?status=eq.open&select=portfolio_id,total_cost_usd"
    )

    # Build per-portfolio summaries
    pf_map = {p["id"]: p for p in portfolios}
    pf_trades = {}
    for t in trades:
        pid = t.get("portfolio_id")
        if pid not in pf_trades:
            pf_trades[pid] = []
        pf_trades[pid].append(t)

    open_map = {}
    for t in open_trades:
        pid = t.get("portfolio_id")
        open_map[pid] = open_map.get(pid, 0) + 1

    prompt = "# Portfolio Dashboard Data\n\n"

    for pf in portfolios:
        pid = pf["id"]
        name = pf.get("name", "?")
        mode = pf.get("trade_mode", "paper")
        closed = pf_trades.get(pid, [])
        n_open = open_map.get(pid, 0)

        if not closed and n_open == 0:
            continue

        won = [t for t in closed if t["status"] == "won"]
        lost = [t for t in closed if t["status"] == "lost"]
        total_invested = sum(float(t.get("total_cost_usd", 0) or 0) for t in closed)
        total_profit = sum(float(t.get("profit_usd", 0) or 0) for t in closed)
        wr = (len(won) / len(closed) * 100) if closed else 0
        roi = (total_profit / total_invested * 100) if total_invested > 0 else 0

        # Safe NO vs Edge breakdown
        sn = [t for t in closed if t.get("bet_type") == "safe_no"]
        sn_won = sum(1 for t in sn if t["status"] == "won")
        sn_pnl = sum(float(t.get("profit_usd", 0) or 0) for t in sn)
        edge = [t for t in closed if t.get("bet_type") == "edge"]
        e_won = sum(1 for t in edge if t["status"] == "won")
        e_pnl = sum(float(t.get("profit_usd", 0) or 0) for t in edge)

        # City breakdown
        city_stats = {}
        for t in closed:
            c = t.get("city", "?")
            if c not in city_stats:
                city_stats[c] = {"won": 0, "lost": 0, "pnl": 0}
            if t["status"] == "won":
                city_stats[c]["won"] += 1
            else:
                city_stats[c]["lost"] += 1
            city_stats[c]["pnl"] += float(t.get("profit_usd", 0) or 0)

        # Strategy summary
        strat = pf.get("strategy", {})
        sides = strat.get("allowed_sides", [])
        bet_types = strat.get("allowed_bet_types", [])
        blocked = strat.get("blocked_cities", [])
        hours = strat.get("trading_hours", {})

        prompt += f"## {name} ({'LIVE' if mode == 'live' else 'PAPER'})\n"
        prompt += f"**Closed:** {len(closed)} ({len(won)}W/{len(lost)}L) | **Open:** {n_open} | **WR:** {wr:.1f}% | **P&L:** ${total_profit:.0f} | **ROI:** {roi:.1f}%\n"
        prompt += f"**Strategy:** sides={sides}, types={bet_types}, blocked={len(blocked)} cities\n"
        if hours.get("enabled"):
            prompt += f"**Trading hours:** enabled, {len(hours.get('allowed_windows', []))} windows, {len(hours.get('blackout_windows', []))} blackouts\n"
        prompt += f"**Safe NO:** {sn_won}W/{len(sn)-sn_won}L, P&L=${sn_pnl:.0f} | **Edge:** {e_won}W/{len(edge)-e_won}L, P&L=${e_pnl:.0f}\n"

        if city_stats:
            top_cities = sorted(city_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)[:5]
            worst_cities = sorted(city_stats.items(), key=lambda x: x[1]["pnl"])[:3]
            top_str = ", ".join(c + " (" + str(d["won"]) + "W/" + str(d["lost"]) + "L $" + str(round(d["pnl"])) + ")" for c, d in top_cities)
            prompt += "**Best cities:** " + top_str + "\n"
            worst_str = ", ".join(c + " ($" + str(round(d["pnl"])) + ")" for c, d in worst_cities if d["pnl"] < 0)
            if worst_str:
                prompt += "**Worst cities:** " + worst_str + "\n"

        prompt += "\n"

    prompt += f"\n**Total portfolios:** {len(portfolios)} | **Total closed trades:** {len(trades)} | **Analysis date:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"

    return prompt


def call_opus(prompt):
    body = json.dumps({
        "model": "claude-opus-4-20250514",
        "max_tokens": 8192,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
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

    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return "\n".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Return history of dashboard analyses."""
        try:
            params = parse_qs(urlparse(self.path).query)
            limit = int(params.get("limit", ["5"])[0])
            data = _supabase_get(
                f"ai_analyses?question=eq.dashboard_analysis&select=*&order=created_at.desc&limit={limit}"
            )
            self._respond(200, data)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_POST(self):
        """Run a new Opus analysis."""
        if not require_auth(self):
            return
        try:
            if not ANTHROPIC_API_KEY:
                self._respond(500, {"error": "ANTHROPIC_API_KEY not set"})
                return

            # Build prompt from live data
            prompt = build_analysis_prompt()

            # Call Opus
            analysis = call_opus(prompt)

            # Save to history
            _supabase_post("ai_analyses", [{
                "question": "dashboard_analysis",
                "analysis": analysis,
                "trade_count": 0,
                "date_range": datetime.utcnow().strftime("%Y-%m-%d"),
                "created_at": datetime.utcnow().isoformat() + "Z",
            }])

            self._respond(200, {"analysis": analysis})

        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:500]
            self._respond(500, {"error": f"Claude API error {e.code}: {err}"})
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
