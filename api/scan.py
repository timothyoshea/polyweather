"""
Vercel Python serverless function — runs the PolyWeather scanner
and saves results to Supabase.

Triggered by:
  - Vercel cron (every 10 minutes)
  - Manual GET /api/scan
"""
import os
import sys
import json
import time
import traceback
from http.server import BaseHTTPRequestHandler

# Add project root to path so scanner modules are importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force scanner into JSON/non-interactive mode
sys.argv = ["scanner.py", "--json"]

import config
config.JSON_OUT = True
config.TOMORROW = False  # scan all dates (tomorrow through 5 days out)
config.DEBUG = False

from scanner import scan
from output import polymarket_url

# Supabase config from env vars
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def supabase_insert(table, rows):
    """Insert rows into a Supabase table using the REST API."""
    import urllib.request
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    data = json.dumps(rows).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_scan_and_save():
    """Run the scanner and persist results to Supabase."""
    t0 = time.time()
    opps = scan()
    duration = round(time.time() - t0, 1)

    # Enrich with URLs
    for opp in opps:
        opp["url"] = polymarket_url(opp)

    sure_bets = sum(1 for o in opps if o.get("bet_type") == "sure")
    edge_bets = sum(1 for o in opps if o.get("bet_type") == "edge")
    safe_no_bets = sum(1 for o in opps if o.get("bet_type") == "safe_no")

    # Insert scan record
    scan_rows = supabase_insert("scans", [{
        "mode": "tomorrow",
        "duration_seconds": duration,
        "total_opportunities": len(opps),
        "sure_bets": sure_bets,
        "edge_bets": edge_bets,
        "safe_no_bets": safe_no_bets,
    }])
    scan_id = scan_rows[0]["id"]

    # Insert opportunities
    if opps:
        opp_rows = []
        for o in opps:
            opp_rows.append({
                "scan_id": scan_id,
                "city": o.get("city", ""),
                "date": o.get("date", ""),
                "side": o.get("side", ""),
                "bet_type": o.get("bet_type", ""),
                "band_c": o.get("band_c", ""),
                "band_f": o.get("band_f", ""),
                "band_type": o.get("band_type", ""),
                "forecast_c": o.get("forecast_c"),
                "my_p": o.get("my_p"),
                "mkt_p": o.get("mkt_p"),
                "edge": o.get("edge"),
                "confidence": o.get("confidence"),
                "ev_per_dollar": o.get("ev_per_dollar"),
                "half_kelly": o.get("hk"),
                "risk": o.get("risk"),
                "question": o.get("question"),
                "token_id": o.get("token_id"),
                "condition_id": o.get("condition_id"),
                "event_slug": o.get("event_slug"),
                "market_slug": o.get("market_slug"),
                "url": o.get("url"),
                "price_source": o.get("price_source"),
                "empirical_p": o.get("empirical_p"),
                "liquidity": o.get("liquidity"),
                "model_values": o.get("model_values"),
                "forecast_details": {
                    k: o.get(k) for k in [
                        "combined_forecast", "ensemble_mean", "ensemble_std",
                        "ensemble_min", "ensemble_max", "multi_model_spread",
                        "eff_std", "horizon_days", "city_tier",
                    ] if o.get(k) is not None
                },
            })
        supabase_insert("opportunities", opp_rows)

    return {
        "scan_id": scan_id,
        "duration_seconds": duration,
        "total": len(opps),
        "sure_bets": sure_bets,
        "edge_bets": edge_bets,
        "safe_no_bets": safe_no_bets,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": "SUPABASE_URL and SUPABASE_SERVICE_KEY env vars required"
                }).encode())
                return

            result = run_scan_and_save()

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
