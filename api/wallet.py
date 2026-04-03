"""
Vercel Python serverless function — wallet balance proxy.

Proxies requests to the Railway trader service so the frontend
can show wallet balance without knowing Railway's URL.

GET /api/wallet?portfolio_id=xxx — returns wallet balance
"""
import os
import json
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

RAILWAY_URL = os.environ.get("RAILWAY_URL", "").rstrip("/")
RAILWAY_API_SECRET = os.environ.get("RAILWAY_API_SECRET", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            portfolio_id = params.get("portfolio_id", [None])[0]

            if not RAILWAY_URL or not RAILWAY_API_SECRET:
                self._respond(200, {
                    "available": False,
                    "message": "Live trading not configured",
                })
                return

            # Check if portfolio is live mode
            if portfolio_id and SUPABASE_URL and SUPABASE_ANON_KEY:
                pf_url = f"{SUPABASE_URL}/rest/v1/portfolios?id=eq.{portfolio_id}&select=trade_mode,wallet_address"
                pf_req = urllib.request.Request(pf_url, headers={
                    "apikey": SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
                })
                with urllib.request.urlopen(pf_req, timeout=5) as resp:
                    pfs = json.loads(resp.read().decode("utf-8"))
                    if pfs and pfs[0].get("trade_mode") != "live":
                        self._respond(200, {
                            "available": False,
                            "message": "Portfolio is in paper mode",
                        })
                        return

            # Proxy to Railway /balance
            req = urllib.request.Request(
                f"{RAILWAY_URL}/balance",
                headers={
                    "Authorization": f"Bearer {RAILWAY_API_SECRET}",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self._respond(200, {"available": True, **data})

        except Exception as e:
            self._respond(200, {
                "available": False,
                "error": str(e),
            })

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
