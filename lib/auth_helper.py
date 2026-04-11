"""
Shared authentication helper for Vercel serverless functions.

Verifies pw_session cookie by calling Supabase Auth /auth/v1/user.
Also supports CRON_SECRET for Vercel cron job bypass.

Usage in any endpoint:
    from auth_helper import require_auth
    # At the start of do_GET / do_POST:
    if not require_auth(self):
        return  # 401 already sent
"""
import os
import json
import urllib.request
import urllib.error

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()
CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()


def _parse_cookie(cookie_header, name):
    """Extract a named value from a Cookie header string."""
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(f"{name}="):
            return part[len(name) + 1:]
    return None


def _verify_token(access_token):
    """Verify a Supabase JWT by calling the user endpoint. Returns True if valid."""
    if not access_token or not SUPABASE_URL:
        return False
    url = f"{SUPABASE_URL}/auth/v1/user"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {access_token}",
    }, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            user = json.loads(resp.read().decode("utf-8"))
            return bool(user.get("email"))
    except (urllib.error.HTTPError, urllib.error.URLError, Exception):
        return False


def _try_refresh(refresh_token):
    """Attempt to refresh an expired access token. Returns new access token or None."""
    if not refresh_token or not SUPABASE_URL:
        return None
    try:
        url = f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token"
        body = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={
            "apikey": SUPABASE_ANON_KEY,
            "Content-Type": "application/json",
        }, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            new_token = data.get("access_token")
            if new_token and _verify_token(new_token):
                return new_token
    except Exception:
        pass
    return None


def verify_session(headers):
    """
    Check if the request is authenticated.

    Checks in order:
    1. CRON_SECRET bypass (for Vercel cron jobs)
    2. pw_session cookie (Supabase JWT)
    3. pw_refresh cookie (attempt token refresh)

    Returns (authenticated: bool, new_access_token: str or None).
    new_access_token is set only when a refresh was performed, so the
    caller can set an updated cookie if desired.
    """
    # 1. Cron secret bypass
    if CRON_SECRET:
        auth_header = headers.get("Authorization", "")
        if auth_header == f"Bearer {CRON_SECRET}":
            return True, None

    # 2. Check pw_session cookie
    cookie_header = headers.get("Cookie", "")
    token = _parse_cookie(cookie_header, "pw_session")
    if token and _verify_token(token):
        return True, None

    # 3. Try refresh
    refresh = _parse_cookie(cookie_header, "pw_refresh")
    if refresh:
        new_token = _try_refresh(refresh)
        if new_token:
            return True, new_token

    return False, None


def require_auth(handler):
    """
    Gate a request handler behind authentication.

    Call at the top of do_GET/do_POST/do_PATCH/do_DELETE.
    Returns True if authenticated (proceed with handler logic).
    Returns False if not authenticated (401 already sent — caller should return).

    If a token refresh occurred, sets updated pw_session cookie on the response.
    """
    authenticated, new_token = verify_session(handler.headers)

    if not authenticated:
        handler.send_response(401)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        handler.wfile.write(json.dumps({
            "error": "Authentication required",
            "login_url": "/login",
        }).encode())
        return False

    # If we refreshed the token, set updated cookie
    if new_token:
        handler.send_header("Set-Cookie",
            f"pw_session={new_token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")

    return True
