"""
Vercel Python serverless function — wallet proxy.

Proxies wallet balance and register requests to Railway,
keeping the Railway API secret server-side.

POST /api/wallet_proxy  { "action": "balance", "address": "0x..." }
POST /api/wallet_proxy  { "action": "register", "address": "0x...", "private_key": "...", "label": "..." }
"""
import os
import sys
import json
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

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

RAILWAY_URL = os.environ.get("RAILWAY_URL", "").rstrip("/")
RAILWAY_API_SECRET = os.environ.get("RAILWAY_API_SECRET", "")


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            # Auth check
            cookie_header = self.headers.get("Cookie", "")
            if not _verify_session(cookie_header):
                self._respond(401, {"error": "Unauthorized"})
                return

            if not RAILWAY_URL or not RAILWAY_API_SECRET:
                self._respond(500, {"error": "Railway not configured"})
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length).decode("utf-8")) if content_length > 0 else {}
            action = body.get("action", "")

            if action == "balance":
                address = body.get("address", "")
                if not address:
                    self._respond(400, {"error": "address required"})
                    return
                url = f"{RAILWAY_URL}/wallets/balance?address={urllib.request.quote(address)}"
                req = urllib.request.Request(url, headers={
                    "Authorization": f"Bearer {RAILWAY_API_SECRET}",
                })
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self._respond(200, data)

            elif action == "register":
                address = body.get("address", "")
                private_key = body.get("private_key", "")
                if not address or not private_key:
                    self._respond(400, {"error": "address and private_key required"})
                    return
                url = f"{RAILWAY_URL}/wallets/register"
                payload = json.dumps({
                    "address": address,
                    "private_key": private_key,
                }).encode("utf-8")
                req = urllib.request.Request(url, data=payload, headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {RAILWAY_API_SECRET}",
                }, method="POST")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self._respond(200, data)

            else:
                self._respond(400, {"error": f"Unknown action: {action}"})

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
            self._respond(e.code, {"error": err_body})
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _respond(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
