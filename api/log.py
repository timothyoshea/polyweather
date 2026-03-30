"""
Vercel Python serverless function — returns scan log/diagnostics.

Since Vercel functions are stateless, this returns the most recent scan
metadata from Supabase as a formatted log.
"""
import os
import json
import urllib.request
from http.server import BaseHTTPRequestHandler

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if not SUPABASE_URL or not SUPABASE_ANON_KEY:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"log": "Supabase not configured."}).encode())
                return

            url = f"{SUPABASE_URL}/rest/v1/scans?order=created_at.desc&limit=5"
            headers = {
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            }
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                scans = json.loads(resp.read().decode("utf-8"))

            if not scans:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"log": "No scans yet."}).encode())
                return

            lines = []
            for s in scans:
                ts = s.get("created_at", "?")
                dur = s.get("duration_seconds", "?")
                total = s.get("total_opportunities", 0)
                sure = s.get("sure_bets", 0)
                edge = s.get("edge_bets", 0)
                safe = s.get("safe_no_bets", 0)
                mode = s.get("mode", "?")
                lines.append(
                    f"[{ts}] mode={mode} | {total} opps "
                    f"(sure={sure}, edge={edge}, safe_no={safe}) | {dur}s"
                )

            log_text = "\n".join(lines)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"log": log_text}).encode())

        except Exception as e:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"log": f"Error fetching log: {e}"}).encode())
