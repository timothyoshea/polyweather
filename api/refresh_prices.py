"""
Vercel Python serverless function — refreshes live prices for open trades.

GET /api/refresh_prices                  — refresh all open trades
GET /api/refresh_prices?portfolio_id=xxx — refresh open trades for a portfolio

Fetches current midpoint prices from Polymarket CLOB for each unique token_id,
updates mkt_p in Supabase, and returns trades with unrealized P&L.
"""
import os
import json
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


def supabase_get(path):
    """Query Supabase REST API with service key."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def supabase_patch(path, data):
    """PATCH update to Supabase REST API."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
    urllib.request.urlopen(req, timeout=10)


def fetch_midpoint(token_id):
    """Fetch current midpoint price from Polymarket CLOB."""
    url = f"https://clob.polymarket.com/midpoint?token_id={token_id}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "PolyWeather/1.0")
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        # Response is {"mid": "0.65"} — a string decimal 0-1
        return float(data.get("mid", 0))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            portfolio_id = params.get("portfolio_id", [None])[0]

            # 1. Fetch all open trades
            query = "paper_trades?status=eq.open&select=*&order=created_at.desc"
            if portfolio_id:
                query += f"&portfolio_id=eq.{portfolio_id}"
            trades = supabase_get(query)

            if not trades:
                self._respond(200, {"trades": [], "refreshed": 0})
                return

            # 2. Collect unique token_ids and batch-fetch midpoints
            midpoint_cache = {}
            for t in trades:
                tid = t.get("token_id")
                if tid and tid not in midpoint_cache:
                    try:
                        midpoint_cache[tid] = fetch_midpoint(tid)
                    except Exception as e:
                        print(f"[WARN] Failed to fetch midpoint for {tid}: {e}")
                        midpoint_cache[tid] = None

            # 3. Update each trade with fresh mkt_p and calculate unrealized P&L
            updated_trades = []
            for t in trades:
                tid = t.get("token_id")
                midpoint = midpoint_cache.get(tid) if tid else None

                if midpoint is not None:
                    # midpoint is 0-1 scale, mkt_p is 0-100 scale
                    new_mkt_p = round(midpoint * 100, 2)

                    # Update mkt_p in Supabase
                    try:
                        supabase_patch(
                            f"paper_trades?id=eq.{t['id']}",
                            {"mkt_p": new_mkt_p},
                        )
                        t["mkt_p"] = new_mkt_p
                    except Exception as e:
                        print(f"[WARN] Failed to update mkt_p for trade {t['id']}: {e}")

                # Calculate unrealized P&L
                total_shares = float(t.get("total_shares", 0) or 0)
                total_cost = float(t.get("total_cost_usd", 0) or 0)
                mkt_p = float(t.get("mkt_p", 0) or 0)

                # token_id is side-specific: YES trades store YES token,
                # NO trades store NO token. The midpoint is already for the
                # correct token, so value = shares * midpoint for both sides.
                current_value = total_shares * (mkt_p / 100)

                unrealized_pnl = current_value - total_cost
                unrealized_pnl_pct = (
                    (unrealized_pnl / total_cost * 100) if total_cost > 0 else 0.0
                )

                t["current_value"] = round(current_value, 4)
                t["unrealized_pnl"] = round(unrealized_pnl, 4)
                t["unrealized_pnl_pct"] = round(unrealized_pnl_pct, 2)
                updated_trades.append(t)

            self._respond(
                200,
                {
                    "trades": updated_trades,
                    "refreshed": len(midpoint_cache),
                    "token_ids_fetched": len(midpoint_cache),
                },
            )

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _respond(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
