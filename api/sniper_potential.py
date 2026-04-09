"""
Vercel Python serverless function — returns sniper potential trades from Supabase.

GET /api/sniper_potential                   — last 24h (default)
GET /api/sniper_potential?hours=48          — custom lookback
GET /api/sniper_potential?city=London       — filter by city
GET /api/sniper_potential?traded=true       — only traded
GET /api/sniper_potential?traded=false      — only skipped
GET /api/sniper_potential?limit=200         — custom limit (default 200)
"""
import os
import json
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

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


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            hours = params.get("hours", ["24"])[0]
            city = params.get("city", [None])[0]
            traded = params.get("traded", [None])[0]
            limit = params.get("limit", ["200"])[0]

            query = (
                f"sniper_potential_trades?select=*"
                f"&order=signal_time.desc"
                f"&limit={limit}"
                f"&signal_time=gte.now()-{hours}h"
            )

            # Supabase REST doesn't support now()-Xh natively; use a filter workaround
            # Actually, use the proper PostgREST interval syntax
            from datetime import datetime, timezone, timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=int(hours))).isoformat()
            query = (
                f"sniper_potential_trades?select=*"
                f"&order=signal_time.desc"
                f"&limit={limit}"
                f"&signal_time=gte.{cutoff}"
            )

            if city:
                query += f"&city=ilike.*{city}*"
            if traded is not None:
                query += f"&was_traded=eq.{traded}"

            data = supabase_query(query)
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
