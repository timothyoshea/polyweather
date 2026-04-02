"""
Vercel Python serverless function — returns ALL trades across all portfolios.

GET /api/trades_all                 — all trades, newest first
GET /api/trades_all?status=won      — filter by status
GET /api/trades_all?limit=500       — custom limit
"""
import os
import json
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            status = params.get("status", [None])[0]
            limit = int(params.get("limit", ["500"])[0])

            headers = {
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            }

            query = (
                f"{SUPABASE_URL}/rest/v1/paper_trades"
                f"?select=id,city,date,band_c,band_type,side,bet_type,status,"
                f"entry_price,total_cost_usd,total_shares,profit_usd,roi_pct,"
                f"portfolio_id,created_at,resolved_at"
                f"&order=created_at.desc&limit={limit}"
            )
            if status:
                query += f"&status=eq.{status}"

            req = urllib.request.Request(query, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                trades = json.loads(resp.read().decode("utf-8"))

            # Also fetch portfolio names for display
            pf_query = f"{SUPABASE_URL}/rest/v1/portfolios?select=id,name"
            pf_req = urllib.request.Request(pf_query, headers=headers, method="GET")
            with urllib.request.urlopen(pf_req, timeout=10) as resp:
                portfolios = json.loads(resp.read().decode("utf-8"))

            pf_map = {p["id"]: p["name"] for p in portfolios}

            # Add portfolio name to each trade
            for t in trades:
                t["portfolio_name"] = pf_map.get(t.get("portfolio_id", ""), "Unknown")

            self._respond(200, trades)

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
