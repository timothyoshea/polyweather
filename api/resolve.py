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

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not _require_auth(self):
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
