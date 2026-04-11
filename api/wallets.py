"""
Vercel Python serverless function — wallet management.

GET    /api/wallets              — list all wallets
POST   /api/wallets              — add a new wallet
PATCH  /api/wallets?id=xxx       — update wallet label or active status
DELETE /api/wallets?id=xxx       — soft-delete a wallet (set active=false)

NOTE: Private keys are NEVER stored here. They are managed separately on Railway.
"""
import os
import sys
import json
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# --- Inline auth check (Vercel serverless can't import across files) ---
def _require_auth(handler):
    """Verify pw_session cookie or CRON_SECRET. Returns True if auth OK, False if 401 sent."""
    import urllib.request as _ur, urllib.error as _ue
    _SUPA_URL = os.environ.get("SUPABASE_URL", "").strip()
    _SUPA_ANON = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    _CRON = os.environ.get("CRON_SECRET", "").strip()

    # 1. Cron secret bypass
    if _CRON and handler.headers.get("Authorization", "") == f"Bearer {_CRON}":
        return True

    # 2. Parse pw_session cookie
    cookie = handler.headers.get("Cookie", "")
    token = None
    for part in cookie.split(";"):
        p = part.strip()
        if p.startswith("pw_session="):
            token = p[11:]
            break

    # 3. Verify token with Supabase
    if token and _SUPA_URL:
        try:
            req = _ur.Request(f"{_SUPA_URL}/auth/v1/user", headers={
                "apikey": _SUPA_ANON, "Authorization": f"Bearer {token}",
            })
            with _ur.urlopen(req, timeout=10) as resp:
                user = json.loads(resp.read().decode())
                if user.get("email"):
                    return True
        except Exception:
            pass

    # 4. Try refresh
    refresh = None
    for part in cookie.split(";"):
        p = part.strip()
        if p.startswith("pw_refresh="):
            refresh = p[11:]
            break
    if refresh and _SUPA_URL:
        try:
            body = json.dumps({"refresh_token": refresh}).encode()
            req = _ur.Request(f"{_SUPA_URL}/auth/v1/token?grant_type=refresh_token",
                data=body, headers={"apikey": _SUPA_ANON, "Content-Type": "application/json"})
            with _ur.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                new_token = data.get("access_token")
                if new_token:
                    return True
        except Exception:
            pass

    # Auth failed
    handler.send_response(401)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(json.dumps({"error": "Authentication required", "login_url": "/login"}).encode())
    return False

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
        if not require_auth(self):
            return
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
        if not require_auth(self):
            return
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
        if not require_auth(self):
            return
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
        if not require_auth(self):
            return
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
