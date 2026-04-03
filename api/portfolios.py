"""
Vercel Python serverless function — portfolio management.

GET  /api/portfolios              — list all portfolios
GET  /api/portfolios?id=xxx       — single portfolio with full strategy
POST /api/portfolios              — create new portfolio
PATCH /api/portfolios?id=xxx      — update portfolio
"""
import os
import json
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


def _headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _request(url, data=None, method="GET"):
    encoded = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=encoded, headers=_headers(), method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body.strip() else []


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            portfolio_id = params.get("id", [None])[0]

            if portfolio_id:
                data = _request(
                    f"{SUPABASE_URL}/rest/v1/portfolios?id=eq.{portfolio_id}&select=*"
                )
                self._respond(200, data[0] if data else None)
            else:
                data = _request(
                    f"{SUPABASE_URL}/rest/v1/portfolios?select=id,name,description,active,created_at,starting_capital_usd,unlimited_capital&order=created_at.asc"
                )
                self._respond(200, data)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))

            row = {
                "name": body.get("name", "New Portfolio"),
                "description": body.get("description", ""),
                "strategy": body.get("strategy", {}),
                "active": body.get("active", True),
                "starting_capital_usd": body.get("starting_capital_usd", 0),
                "unlimited_capital": body.get("unlimited_capital", True),
            }
            result = _request(
                f"{SUPABASE_URL}/rest/v1/portfolios",
                data=[row],
                method="POST",
            )
            self._respond(201, result[0] if result else None)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_PATCH(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            portfolio_id = params.get("id", [None])[0]
            if not portfolio_id:
                self._respond(400, {"error": "id parameter required"})
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))

            update = {}
            for key in ("name", "description", "strategy", "active"):
                if key in body:
                    update[key] = body[key]
            update["updated_at"] = datetime.utcnow().isoformat() + "Z"

            result = _request(
                f"{SUPABASE_URL}/rest/v1/portfolios?id=eq.{portfolio_id}",
                data=update,
                method="PATCH",
            )
            self._respond(200, result[0] if result else None)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _respond(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
