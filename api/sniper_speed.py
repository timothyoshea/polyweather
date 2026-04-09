"""
Vercel Python serverless function — speed analysis from sniper_price_tracks.

GET /api/sniper_speed                     — raw tracks (default 24h, limit 100)
GET /api/sniper_speed?summary=true        — aggregate stats overall + by city
GET /api/sniper_speed?city=London         — filter by city
GET /api/sniper_speed?hours=24            — time range (default 24)
"""
import os
import json
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


def supabase_query(path):
    """Query Supabase REST API."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def safe_float(v):
    """Convert to float or return None."""
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def compute_summary(tracks):
    """Compute aggregate stats from a list of tracks."""
    if not tracks:
        return {
            "total_tracks": 0,
            "avg_price_at_signal": None,
            "avg_price_at_30s": None,
            "avg_price_at_1m": None,
            "avg_price_at_2m": None,
            "avg_price_at_5m": None,
            "avg_price_at_10m": None,
            "avg_time_to_95pct_seconds": None,
            "avg_time_to_99pct_seconds": None,
            "pct_under_95_at_signal": 0,
            "pct_under_98_at_signal": 0,
        }

    n = len(tracks)

    def avg_field(field):
        vals = [safe_float(t.get(field)) for t in tracks]
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    price_at_signal_vals = [safe_float(t.get("price_at_signal")) for t in tracks]
    price_at_signal_vals = [v for v in price_at_signal_vals if v is not None]

    under_95 = sum(1 for v in price_at_signal_vals if v < 0.95)
    under_98 = sum(1 for v in price_at_signal_vals if v < 0.98)

    return {
        "total_tracks": n,
        "avg_price_at_signal": avg_field("price_at_signal"),
        "avg_price_at_30s": avg_field("price_at_30s"),
        "avg_price_at_1m": avg_field("price_at_1m"),
        "avg_price_at_2m": avg_field("price_at_2m"),
        "avg_price_at_5m": avg_field("price_at_5m"),
        "avg_price_at_10m": avg_field("price_at_10m"),
        "avg_time_to_95pct_seconds": avg_field("time_to_95pct"),
        "avg_time_to_99pct_seconds": avg_field("time_to_99pct"),
        "pct_under_95_at_signal": round(under_95 / n * 100, 1) if n else 0,
        "pct_under_98_at_signal": round(under_98 / n * 100, 1) if n else 0,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            city = params.get("city", [None])[0]
            hours = int(params.get("hours", ["24"])[0])
            summary = params.get("summary", ["false"])[0].lower() == "true"
            limit = int(params.get("limit", ["200"])[0])

            # Build time filter
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

            query = f"sniper_price_tracks?select=*&order=signal_time.desc&signal_time=gte.{cutoff}&limit={limit}"
            if city:
                query += f"&city=eq.{city}"

            tracks = supabase_query(query)

            if summary:
                # Overall
                overall = compute_summary(tracks)

                # By city
                cities = {}
                for t in tracks:
                    c = t.get("city", "Unknown")
                    cities.setdefault(c, []).append(t)

                by_city = []
                for c, city_tracks in sorted(cities.items()):
                    city_summary = compute_summary(city_tracks)
                    city_summary["city"] = c
                    city_summary["tracks"] = city_summary.pop("total_tracks")
                    by_city.append(city_summary)

                self._respond(200, {"overall": overall, "by_city": by_city})
            else:
                self._respond(200, tracks)

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self._respond(200, {})
