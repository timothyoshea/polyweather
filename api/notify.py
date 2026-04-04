"""
Vercel Python serverless function — trade notification emails.

POST /api/notify — sends email alerts for trade events.

Body: {"action": "trade_executed", "trade": {...}, "portfolio": {...}}

Uses Resend SMTP (smtp.resend.com:587) to send HTML emails.
"""
import os
import json
import smtplib
from http.server import BaseHTTPRequestHandler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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
