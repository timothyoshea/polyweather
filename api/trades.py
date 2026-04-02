"""
Vercel Python serverless function — returns paper trade data from Supabase.

GET /api/trades                     — last 100 trades, newest first
GET /api/trades?status=open         — filter by status (open/won/lost)
GET /api/trades?summary=true        — aggregated P&L summary
GET /api/trades?id=xxx&snapshots=true — single trade + snapshots
"""
import os
import json
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from collections import defaultdict

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


def supabase_query(path):
    """Query Supabase REST API."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_summary(trades):
    """Build aggregated summary from a list of trades."""
    total = len(trades)
    open_count = sum(1 for t in trades if t.get("status") == "open")
    won_count = sum(1 for t in trades if t.get("status") == "won")
    lost_count = sum(1 for t in trades if t.get("status") == "lost")
    void_count = sum(1 for t in trades if t.get("status") == "void")

    resolved = [t for t in trades if t.get("status") in ("won", "lost")]
    resolved_count = won_count + lost_count
    win_rate = (won_count / resolved_count * 100) if resolved_count > 0 else 0.0

    total_invested = sum(float(t.get("total_cost_usd", 0) or 0) for t in resolved)
    total_payout = sum(float(t.get("payout_usd", 0) or 0) for t in resolved)
    total_profit = sum(float(t.get("profit_usd", 0) or 0) for t in resolved)
    roi_pct = (total_profit / total_invested * 100) if total_invested > 0 else 0.0

    # By bet_type
    by_type = {}
    for bet_type in ("sure", "edge", "safe_no"):
        type_trades = [t for t in resolved if t.get("bet_type") == bet_type]
        type_won = sum(1 for t in type_trades if t.get("status") == "won")
        type_profit = sum(float(t.get("profit_usd", 0) or 0) for t in type_trades)
        by_type[bet_type] = {
            "count": len(type_trades),
            "won": type_won,
            "profit": round(type_profit, 2),
        }

    # By city
    by_city = {}
    for t in resolved:
        city = t.get("city", "Unknown")
        if city not in by_city:
            by_city[city] = {"count": 0, "won": 0, "profit": 0.0}
        by_city[city]["count"] += 1
        if t.get("status") == "won":
            by_city[city]["won"] += 1
        by_city[city]["profit"] += float(t.get("profit_usd", 0) or 0)
    # Round profit values
    for city in by_city:
        by_city[city]["profit"] = round(by_city[city]["profit"], 2)

    # Daily P&L
    daily = defaultdict(lambda: {"trades_resolved": 0, "profit": 0.0})
    for t in resolved:
        d = t.get("date", "")
        if d:
            daily[d]["trades_resolved"] += 1
            daily[d]["profit"] += float(t.get("profit_usd", 0) or 0)

    daily_pnl = []
    cumulative = 0.0
    for d in sorted(daily.keys()):
        cumulative += daily[d]["profit"]
        daily_pnl.append({
            "date": d,
            "trades_resolved": daily[d]["trades_resolved"],
            "profit": round(daily[d]["profit"], 2),
            "cumulative": round(cumulative, 2),
        })

    return {
        "total_trades": total,
        "open": open_count,
        "won": won_count,
        "lost": lost_count,
        "void": void_count,
        "win_rate": round(win_rate, 2),
        "total_invested": round(total_invested, 2),
        "total_payout": round(total_payout, 2),
        "total_profit": round(total_profit, 2),
        "roi_pct": round(roi_pct, 2),
        "by_type": by_type,
        "by_city": by_city,
        "daily_pnl": daily_pnl,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            portfolio_id = params.get("portfolio_id", [None])[0]
            pf_filter = f"&portfolio_id=eq.{portfolio_id}" if portfolio_id else ""

            # Single trade + snapshots
            trade_id = params.get("id", [None])[0]
            if trade_id and params.get("snapshots", [None])[0] == "true":
                trade = supabase_query(
                    f"paper_trades?id=eq.{trade_id}&select=*"
                )
                snapshots = supabase_query(
                    f"trade_snapshots?trade_id=eq.{trade_id}&order=created_at.asc"
                )
                data = {
                    "trade": trade[0] if trade else None,
                    "snapshots": snapshots,
                }
                self._respond(200, data)
                return

            # Summary mode
            if params.get("summary", [None])[0] == "true":
                all_trades = supabase_query(
                    f"paper_trades?select=*&order=created_at.desc{pf_filter}"
                )
                data = build_summary(all_trades)
                self._respond(200, data)
                return

            # List trades with optional status/date filters
            status = params.get("status", [None])[0]
            limit = int(params.get("limit", ["100"])[0])
            from_date = params.get("from", [None])[0]
            to_date = params.get("to", [None])[0]
            query = f"paper_trades?select=*&order=created_at.desc{pf_filter}"
            if status:
                query += f"&status=eq.{status}"
            if from_date:
                query += f"&date=gte.{from_date}"
            if to_date:
                query += f"&date=lte.{to_date}"
            query += f"&limit={limit}"

            data = supabase_query(query)
            self._respond(200, data)

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
