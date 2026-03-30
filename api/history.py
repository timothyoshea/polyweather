"""
Vercel Python serverless function — returns scan history from Supabase.

GET /api/history              — last 50 scans
GET /api/history?scan_id=xxx  — opportunities for a specific scan
GET /api/history?limit=10     — control number of results
"""
import os
import json
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


def supabase_query(path):
    """Query Supabase REST API."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            scan_id = params.get("scan_id", [None])[0]
            limit = int(params.get("limit", ["50"])[0])

            if scan_id:
                # Get opportunities for a specific scan
                data = supabase_query(
                    f"opportunities?scan_id=eq.{scan_id}&order=edge.desc"
                )
            else:
                # Get recent scans
                data = supabase_query(
                    f"scans?order=created_at.desc&limit={limit}"
                )

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
