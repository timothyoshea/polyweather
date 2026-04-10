"""
Vercel Python serverless function — returns sniper trades from Supabase.

GET /api/sniper_trades                  — all trades (default limit 50)
GET /api/sniper_trades?status=open      — filter by status (open/won/lost)
GET /api/sniper_trades?limit=100        — custom limit
"""
import os
import json
import re
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


def _safe_int(val, default, min_val=1, max_val=1000):
    try:
        v = int(val)
        return max(min_val, min(v, max_val))
    except (ValueError, TypeError):
        return default


def _safe_city(val):
    if not val:
        return None
    return re.sub(r'[^a-zA-Z0-9 \-]', '', val)[:50]


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
            status = params.get("status", [None])[0]
            limit = _safe_int(params.get("limit", ["50"])[0], 50, 1, 1000)

            query = f"sniper_trades?select=*&order=created_at.desc&limit={limit}"
            if status:
                query += f"&status=eq.{status}"

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
