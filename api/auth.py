"""
Vercel Python serverless function — authentication via Supabase Auth OTP + TOTP MFA.

POST /api/auth  { "action": "send-code", "email": "..." }
POST /api/auth  { "action": "verify", "email": "...", "code": "123456" }
POST /api/auth  { "action": "check" }  (checks session cookie)
POST /api/auth  { "action": "logout" }
POST /api/auth  { "action": "check-mfa", "access_token": "..." }
POST /api/auth  { "action": "enroll-totp", "access_token": "..." }
POST /api/auth  { "action": "challenge-totp", "access_token": "...", "factor_id": "..." }
POST /api/auth  { "action": "verify-totp", "access_token": "...", "factor_id": "...", "challenge_id": "...", "code": "..." }
"""
import os
import json
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler
from http.cookies import SimpleCookie

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

# Only these emails can log in
ALLOWED_EMAILS = {"toshea@gmail.com", "tim@theboost.ai"}


def _supabase_auth_request(path, data):
    """Make a request to Supabase Auth API."""
    url = f"{SUPABASE_URL}/auth/v1/{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "apikey": SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
    }, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _supabase_auth_get(path, access_token):
    """Make an authenticated GET request to Supabase Auth API."""
    url = f"{SUPABASE_URL}/auth/v1/{path}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {access_token}",
    }, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _supabase_auth_post(path, data, access_token):
    """Make an authenticated POST request to Supabase Auth API."""
    url = f"{SUPABASE_URL}/auth/v1/{path}"
    body = json.dumps(data).encode("utf-8") if data else b"{}"
    req = urllib.request.Request(url, data=body, headers={
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _verify_token(access_token):
    """Verify a Supabase JWT by calling the user endpoint."""
    url = f"{SUPABASE_URL}/auth/v1/user"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {access_token}",
    }, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            user = json.loads(resp.read().decode("utf-8"))
            email = user.get("email", "")
            if email in ALLOWED_EMAILS:
                return {"valid": True, "email": email}
            return {"valid": False, "error": "unauthorized email"}
    except urllib.error.HTTPError:
        return {"valid": False, "error": "invalid token"}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length).decode("utf-8")) if content_length > 0 else {}
            action = body.get("action", "")

            if action == "send-code":
                email = (body.get("email") or "").strip().lower()
                if email not in ALLOWED_EMAILS:
                    self._respond(403, {"error": "Email not authorized"})
                    return

                try:
                    result = _supabase_auth_request("otp", {
                        "email": email,
                        "should_create_user": False,
                    })
                    self._respond(200, {"sent": True})
                except urllib.error.HTTPError as e:
                    err_body = e.read().decode("utf-8", errors="replace")
                    self._respond(500, {"error": f"Failed to send code: {err_body[:200]}"})

            elif action == "verify":
                email = (body.get("email") or "").strip().lower()
                code = (body.get("code") or "").strip()

                if not email or not code:
                    self._respond(400, {"error": "email and code required"})
                    return

                if email not in ALLOWED_EMAILS:
                    self._respond(403, {"error": "Email not authorized"})
                    return

                try:
                    result = _supabase_auth_request("verify", {
                        "type": "email",
                        "email": email,
                        "token": code,
                    })

                    access_token = result.get("access_token")
                    refresh_token = result.get("refresh_token", "")
                    if not access_token:
                        self._respond(401, {"error": "Invalid code"})
                        return

                    # Set session cookies — both access and refresh token
                    remember = body.get("remember", False)
                    max_age = 2592000 if remember else 604800  # 30 days or 7 days
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Set-Cookie",
                        f"pw_session={access_token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}")
                    self.send_header("Set-Cookie",
                        f"pw_refresh={refresh_token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "authenticated": True,
                        "email": email,
                    }).encode())
                    return

                except urllib.error.HTTPError as e:
                    err_body = e.read().decode("utf-8", errors="replace")
                    if e.code == 401 or "invalid" in err_body.lower():
                        self._respond(401, {"error": "Invalid or expired code"})
                    else:
                        self._respond(500, {"error": f"Verification failed: {err_body[:200]}"})

            elif action == "check":
                cookie_header = self.headers.get("Cookie", "")
                token = None
                refresh = None
                for part in cookie_header.split(";"):
                    part = part.strip()
                    if part.startswith("pw_session="):
                        token = part[len("pw_session="):]
                    elif part.startswith("pw_refresh="):
                        refresh = part[len("pw_refresh="):]

                if not token and not refresh:
                    self._respond(200, {"authenticated": False})
                    return

                # Try the access token first
                if token:
                    result = _verify_token(token)
                    if result.get("valid"):
                        self._respond(200, {"authenticated": True, "email": result["email"]})
                        return

                # Access token expired — try refresh
                if refresh:
                    try:
                        refresh_result = _supabase_auth_request("token?grant_type=refresh_token", {
                            "refresh_token": refresh,
                        })
                        new_access = refresh_result.get("access_token")
                        new_refresh = refresh_result.get("refresh_token", refresh)

                        if new_access:
                            # Verify the new token
                            verify = _verify_token(new_access)
                            if verify.get("valid"):
                                # Set new cookies with refreshed tokens
                                self.send_response(200)
                                self.send_header("Content-Type", "application/json")
                                self.send_header("Access-Control-Allow-Origin", "*")
                                self.send_header("Set-Cookie",
                                    f"pw_session={new_access}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")
                                self.send_header("Set-Cookie",
                                    f"pw_refresh={new_refresh}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")
                                self.end_headers()
                                self.wfile.write(json.dumps({
                                    "authenticated": True,
                                    "email": verify["email"],
                                }).encode())
                                return
                    except Exception as refresh_err:
                        print(f"[AUTH] Refresh failed: {refresh_err}")

                # Both failed — clear cookies
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "pw_session=; Path=/; HttpOnly; Max-Age=0")
                self.send_header("Set-Cookie", "pw_refresh=; Path=/; HttpOnly; Max-Age=0")
                self.end_headers()
                self.wfile.write(json.dumps({"authenticated": False}).encode())

            elif action == "logout":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Set-Cookie", "pw_session=; Path=/; HttpOnly; Max-Age=0")
                self.send_header("Set-Cookie", "pw_refresh=; Path=/; HttpOnly; Max-Age=0")
                self.end_headers()
                self.wfile.write(json.dumps({"logged_out": True}).encode())

            else:
                self._respond(400, {"error": f"Unknown action: {action}"})

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
