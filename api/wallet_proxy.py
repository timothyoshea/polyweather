"""
Vercel Python serverless function — wallet proxy.

Proxies wallet balance and register requests to Railway,
keeping the Railway API secret server-side.

POST /api/wallet_proxy  { "action": "balance", "address": "0x..." }
POST /api/wallet_proxy  { "action": "register", "address": "0x...", "private_key": "...", "label": "..." }
"""
import os
import json
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

RAILWAY_URL = os.environ.get("RAILWAY_URL", "").rstrip("/")
RAILWAY_API_SECRET = os.environ.get("RAILWAY_API_SECRET", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()


def _verify_session(cookie_header):
    """Verify pw_session cookie via Supabase Auth."""
    token = None
    for part in (cookie_header or "").split(";"):
        part = part.strip()
        if part.startswith("pw_session="):
            token = part[len("pw_session="):]
            break
    if not token:
        return False
    try:
        url = f"{SUPABASE_URL}/auth/v1/user"
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {token}",
        }, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            user = json.loads(resp.read().decode("utf-8"))
            return bool(user.get("email"))
    except Exception:
        return False


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
