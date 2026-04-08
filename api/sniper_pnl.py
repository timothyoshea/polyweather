"""
Vercel Python serverless function — P&L summary for sniper trades.

GET /api/sniper_pnl — returns overall stats, breakdown by trade_type, by day, and signal accuracy
"""
import os
import json
import urllib.request
from http.server import BaseHTTPRequestHandler
from collections import defaultdict

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


def supabase_query(path):
    """Query Supabase REST API."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_pnl(trades, signals):
    """Build P&L summary from trades and signals."""
    total = len(trades)
    wins = sum(1 for t in trades if t["status"] == "won")
    losses = sum(1 for t in trades if t["status"] == "lost")
    open_count = sum(1 for t in trades if t["status"] == "open")

    resolved = [t for t in trades if t["status"] in ("won", "lost")]
    profits = [float(t.get("profit_usd") or 0) for t in resolved]
    total_profit = round(sum(profits), 2)
    avg_profit = round(total_profit / len(resolved), 2) if resolved else 0
    win_rate = round(wins / len(resolved) * 100, 1) if resolved else 0

    # Breakdown by trade_type
    by_type = defaultdict(lambda: {"total": 0, "wins": 0, "losses": 0, "profit": 0})
    for t in trades:
        tt = t.get("trade_type", "unknown")
        by_type[tt]["total"] += 1
        if t["status"] == "won":
            by_type[tt]["wins"] += 1
        elif t["status"] == "lost":
            by_type[tt]["losses"] += 1
        if t["status"] in ("won", "lost"):
            by_type[tt]["profit"] += float(t.get("profit_usd") or 0)

    type_breakdown = []
    for tt, d in sorted(by_type.items()):
        resolved_count = d["wins"] + d["losses"]
        type_breakdown.append({
            "trade_type": tt,
            "total": d["total"],
            "wins": d["wins"],
            "losses": d["losses"],
            "profit_usd": round(d["profit"], 2),
            "win_rate": round(d["wins"] / resolved_count * 100, 1) if resolved_count else 0,
        })

    # Breakdown by day
    by_day = defaultdict(lambda: {"total": 0, "wins": 0, "losses": 0, "profit": 0})
    for t in trades:
        day = (t.get("created_at") or "")[:10]
        if not day:
            continue
        by_day[day]["total"] += 1
        if t["status"] == "won":
            by_day[day]["wins"] += 1
        elif t["status"] == "lost":
            by_day[day]["losses"] += 1
        if t["status"] in ("won", "lost"):
            by_day[day]["profit"] += float(t.get("profit_usd") or 0)

    day_breakdown = []
    for day, d in sorted(by_day.items(), reverse=True):
        day_breakdown.append({
            "date": day,
            "total": d["total"],
            "wins": d["wins"],
            "losses": d["losses"],
            "profit_usd": round(d["profit"], 2),
        })

    # Signal accuracy
    total_signals = len(signals)
    traded_signals = sum(1 for s in signals if s.get("traded"))
    signal_accuracy = round(traded_signals / total_signals * 100, 1) if total_signals else 0

    return {
        "overview": {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "open": open_count,
            "total_profit_usd": total_profit,
            "avg_profit_per_trade": avg_profit,
            "win_rate": win_rate,
        },
        "by_trade_type": type_breakdown,
        "by_day": day_breakdown,
        "signal_accuracy": {
            "total_signals": total_signals,
            "traded_signals": traded_signals,
            "accuracy_pct": signal_accuracy,
        },
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            trades = supabase_query("sniper_trades?select=*")
            signals = supabase_query("sniper_signals?select=id,traded")
            data = build_pnl(trades, signals)
            self._respond(200, data)

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self._respond(200, {})
