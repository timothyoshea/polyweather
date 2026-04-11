"""
Vercel Python serverless function — trade notification emails.

POST /api/notify — sends email alerts for trade events.

Body: {"action": "trade_executed", "trade": {...}, "portfolio": {...}}

Uses Resend SMTP (smtp.resend.com:587) to send HTML emails.
"""
import os
import sys
import json
import smtplib
from http.server import BaseHTTPRequestHandler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
DASHBOARD_URL = "https://polyweather.vercel.app"
RECIPIENT = "tim@theboost.ai"
SENDER = "PolyWeather Alerts <onboarding@resend.dev>"
SENDER_EMAIL = "onboarding@resend.dev"


def _send_trade_email(trade, portfolio):
    """Build and send an HTML email for an executed trade."""
    city = trade.get("city", "?")
    band_c = trade.get("band_c", "?")
    side = trade.get("side", "?")
    cost = float(trade.get("total_cost_usd", 0) or 0)
    shares = float(trade.get("total_shares", 0) or 0)
    entry_price = float(trade.get("entry_price", 0) or 0)
    edge = trade.get("edge", "?")
    confidence = trade.get("confidence", "?")
    bet_type = trade.get("bet_type", "?")
    date = trade.get("date", "?")
    trade_mode = trade.get("trade_mode", "live")
    pf_name = portfolio.get("name", "?") if portfolio else "?"

    subject = f"Trade Executed: {city} {band_c} {side} — ${cost:.2f}"

    html = f"""
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px;">
  <div style="max-width: 500px; margin: 0 auto; background: #16213e; border-radius: 8px; padding: 24px; border: 1px solid #0f3460;">
    <h2 style="color: #00d2ff; margin-top: 0;">Trade Executed</h2>
    <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
      <tr><td style="padding: 8px 0; color: #999;">City</td><td style="padding: 8px 0; text-align: right; font-weight: 600;">{city}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Date</td><td style="padding: 8px 0; text-align: right;">{date}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Band</td><td style="padding: 8px 0; text-align: right;">{band_c}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Side</td><td style="padding: 8px 0; text-align: right;">{side}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Type</td><td style="padding: 8px 0; text-align: right;">{bet_type}</td></tr>
      <tr style="border-top: 1px solid #0f3460;">
        <td style="padding: 8px 0; color: #999;">Cost</td>
        <td style="padding: 8px 0; text-align: right; font-weight: 600; color: #00d2ff;">${cost:.2f}</td>
      </tr>
      <tr><td style="padding: 8px 0; color: #999;">Shares</td><td style="padding: 8px 0; text-align: right;">{shares:.2f}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Entry Price</td><td style="padding: 8px 0; text-align: right;">{entry_price:.4f}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Edge</td><td style="padding: 8px 0; text-align: right;">{edge}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Confidence</td><td style="padding: 8px 0; text-align: right;">{confidence}</td></tr>
    </table>
    <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #0f3460; font-size: 13px; color: #999;">
      <strong>Portfolio:</strong> {pf_name} &nbsp;|&nbsp; <strong>Mode:</strong> {trade_mode}
    </div>
    <div style="margin-top: 16px;">
      <a href="{DASHBOARD_URL}" style="color: #00d2ff; text-decoration: none;">View Dashboard &rarr;</a>
    </div>
  </div>
</body>
</html>
"""

    msg = MIMEMultipart("alternative")
    msg["From"] = SENDER
    msg["To"] = RECIPIENT
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP("smtp.resend.com", 587) as server:
        server.starttls()
        server.login("resend", RESEND_API_KEY)
        server.sendmail(SENDER_EMAIL, RECIPIENT, msg.as_string())

    return subject


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not _require_auth(self):
            return
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length)) if content_length else {}

            action = body.get("action", "")
            trade = body.get("trade", {})
            portfolio = body.get("portfolio", {})

            if not RESEND_API_KEY:
                self._respond(500, {"error": "RESEND_API_KEY not configured"})
                return

            if action == "trade_executed":
                subject = _send_trade_email(trade, portfolio)
                self._respond(200, {"ok": True, "subject": subject})
            else:
                self._respond(400, {"error": f"Unknown action: {action}"})

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
