"""
Vercel Python serverless function — resolves open paper trades
against actual temperatures from Open-Meteo.

Triggered by:
  - Vercel cron (every 2 hours)
  - Manual GET /api/resolve
"""
import os
import sys
import json
import traceback
from http.server import BaseHTTPRequestHandler

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_trading import resolve_open_trades
from config import CITY_GEO
from lib.auth_helper import require_auth

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not require_auth(self):
            return
        try:
            if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": "SUPABASE_URL and SUPABASE_SERVICE_KEY env vars required"
                }).encode())
                return

            result = resolve_open_trades(SUPABASE_URL, SUPABASE_SERVICE_KEY, CITY_GEO)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": str(e),
                "traceback": traceback.format_exc(),
            }).encode())
