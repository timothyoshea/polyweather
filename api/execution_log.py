"""
Vercel Python serverless function — execution log viewer.

GET /api/execution_log                      — last 50 log entries
GET /api/execution_log?portfolio_id=xxx     — filter by portfolio
GET /api/execution_log?action=trade_executed — filter by action
GET /api/execution_log?limit=100            — custom limit
"""
import os
import json
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            portfolio_id = params.get("portfolio_id", [None])[0]
            action = params.get("action", [None])[0]
            limit = int(params.get("limit", ["50"])[0])

            url = (
                f"{SUPABASE_URL}/rest/v1/execution_log"
                f"?select=*&order=created_at.desc&limit={limit}"
            )
            if portfolio_id:
                url += f"&portfolio_id=eq.{portfolio_id}"
            if action:
                url += f"&action=eq.{action}"

            req = urllib.request.Request(url, headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            self._respond(200, data)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_OPTIONS(self):
        self.send_response(200)
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
