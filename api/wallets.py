"""
Vercel Python serverless function — wallet management.

GET    /api/wallets              — list all wallets
POST   /api/wallets              — add a new wallet
PATCH  /api/wallets?id=xxx       — update wallet label or active status
DELETE /api/wallets?id=xxx       — soft-delete a wallet (set active=false)

NOTE: Private keys are NEVER stored here. They are managed separately on Railway.
"""
import os
import json
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _auth_helper import require_auth

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
            wallet_id = params.get("id", [None])[0]

            if wallet_id:
                data = _request(
                    f"{SUPABASE_URL}/rest/v1/wallets?id=eq.{wallet_id}&select=*"
                )
                self._respond(200, data[0] if data else None)
            else:
                data = _request(
                    f"{SUPABASE_URL}/rest/v1/wallets?select=id,label,address,active,created_at&order=created_at.asc"
                )
                self._respond(200, data)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))

            address = body.get("address", "").strip()
            label = body.get("label", "").strip()

            if not label:
                self._respond(400, {"error": "label is required"})
                return
            if not address.startswith("0x") or len(address) != 42:
                self._respond(400, {"error": "Invalid address: must start with 0x and be 42 characters"})
                return

            row = {
                "label": label,
                "address": address,
                "active": True,
            }
            result = _request(
                f"{SUPABASE_URL}/rest/v1/wallets",
                data=[row],
                method="POST",
            )
            self._respond(201, result[0] if result else None)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_PATCH(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            wallet_id = params.get("id", [None])[0]
            if not wallet_id:
                self._respond(400, {"error": "id parameter required"})
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))

            update = {}
            for key in ("label", "active"):
                if key in body:
                    update[key] = body[key]

            if not update:
                self._respond(400, {"error": "Nothing to update"})
                return

            result = _request(
                f"{SUPABASE_URL}/rest/v1/wallets?id=eq.{wallet_id}",
                data=update,
                method="PATCH",
            )
            self._respond(200, result[0] if result else None)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_DELETE(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            wallet_id = params.get("id", [None])[0]
            if not wallet_id:
                self._respond(400, {"error": "id parameter required"})
                return

            # Soft delete: set active=false
            result = _request(
                f"{SUPABASE_URL}/rest/v1/wallets?id=eq.{wallet_id}",
                data={"active": False},
                method="PATCH",
            )
            self._respond(200, result[0] if result else None)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _respond(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
