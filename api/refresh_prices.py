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

            # 1. Fetch all open trades (lightweight columns + what UI needs)
            query = "paper_trades?status=eq.open&select=id,city,date,band_c,band_f,band_type,side,bet_type,entry_price,total_cost_usd,total_shares,edge,confidence,mkt_p,my_p,ev_per_dollar,half_kelly,forecast_c,risk,status,trade_mode,token_id,condition_id,event_slug,market_slug,url,portfolio_id,created_at,forecast_details,liquidity&order=created_at.desc"
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

            # 3. Calculate unrealized P&L using live prices
            # IMPORTANT: We do NOT update mkt_p in Supabase — trade snapshots
            # are immutable for historical analysis. Live prices are returned
            # alongside original trade data for comparison.
            updated_trades = []
            for t in trades:
                tid = t.get("token_id")
                midpoint = midpoint_cache.get(tid) if tid else None

                total_shares = float(t.get("total_shares", 0) or 0)
                total_cost = float(t.get("total_cost_usd", 0) or 0)
                original_mkt_p = float(t.get("mkt_p", 0) or 0)

                if midpoint is not None:
                    live_price = round(midpoint * 100, 2)
                else:
                    live_price = original_mkt_p

                # token_id is side-specific: midpoint is already the correct
                # price for both YES and NO tokens
                current_value = total_shares * (live_price / 100)
                entry_value = total_cost

                unrealized_pnl = current_value - entry_value
                unrealized_pnl_pct = (
                    (unrealized_pnl / entry_value * 100) if entry_value > 0 else 0.0
                )

                # Price movement since entry
                entry_price = float(t.get("entry_price", 0) or 0)
                price_move = live_price - (entry_price * 100) if entry_price else 0

                t["live_price"] = live_price
                t["original_mkt_p"] = original_mkt_p
                t["current_value"] = round(current_value, 4)
                t["unrealized_pnl"] = round(unrealized_pnl, 4)
                t["unrealized_pnl_pct"] = round(unrealized_pnl_pct, 2)
                t["price_move"] = round(price_move, 2)
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
