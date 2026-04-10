"""
Vercel Python serverless function — returns METAR temperature history from Supabase.

GET /api/metar_history                     — last 24 hours, all stations
GET /api/metar_history?station=EHAM        — filter by ICAO station
GET /api/metar_history?city=Amsterdam      — filter by city
GET /api/metar_history?hours=48            — how far back (default 24)
GET /api/metar_history?limit=500           — max results (default 500)
GET /api/metar_history?summary=true        — per-station summary
"""
import os
import json
import re
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
from datetime import datetime, timedelta, timezone


def _safe_int(val, default, min_val=1, max_val=1000):
    try:
        v = int(val)
        return max(min_val, min(v, max_val))
    except (ValueError, TypeError):
        return default


def _safe_city(val):
    if not val:
        return None
    return re.sub(r'[^a-zA-Z0-9 \-]', '', val)[:50]


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
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_summary(rows):
    """Build per-station summary from raw readings."""
    stations = {}
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    for r in rows:
        key = r.get("station", "UNKNOWN")
        if key not in stations:
            stations[key] = {
                "station": key,
                "city": r.get("city", ""),
                "latest_temp_c": None,
                "today_high": None,
                "today_low": None,
                "reading_count": 0,
                "timestamps": [],
                "last_update": None,
                "resolution_source": r.get("resolution_source", ""),
            }

        s = stations[key]
        temp = r.get("temp_c")
        ts_str = r.get("polled_at") or r.get("observed_at") or ""

        # Parse timestamp
        ts = None
        if ts_str:
            try:
                ts_str_clean = ts_str.replace("Z", "+00:00")
                ts = datetime.fromisoformat(ts_str_clean)
            except Exception:
                pass

        # Track latest reading (rows come ordered desc, first is latest)
        if s["latest_temp_c"] is None and temp is not None:
            s["latest_temp_c"] = temp
        if ts and (s["last_update"] is None):
            s["last_update"] = ts_str

        # Update resolution_source if empty and this row has it
        if not s["resolution_source"] and r.get("resolution_source"):
            s["resolution_source"] = r["resolution_source"]

        # Today's high/low
        if ts and ts >= today_start and temp is not None:
            s["reading_count"] += 1
            s["timestamps"].append(ts)
            if s["today_high"] is None or temp > s["today_high"]:
                s["today_high"] = temp
            if s["today_low"] is None or temp < s["today_low"]:
                s["today_low"] = temp

    # Calculate avg update interval
    result = []
    for s in stations.values():
        avg_interval = None
        if len(s["timestamps"]) >= 2:
            sorted_ts = sorted(s["timestamps"])
            deltas = [(sorted_ts[i + 1] - sorted_ts[i]).total_seconds()
                      for i in range(len(sorted_ts) - 1)]
            avg_interval = round(sum(deltas) / len(deltas) / 60, 1)  # minutes

        result.append({
            "station": s["station"],
            "city": s["city"],
            "latest_temp_c": s["latest_temp_c"],
            "today_high": s["today_high"],
            "today_low": s["today_low"],
            "reading_count": s["reading_count"],
            "avg_interval_min": avg_interval,
            "last_update": s["last_update"],
            "resolution_source": s["resolution_source"],
        })

    # Sort by city name
    result.sort(key=lambda x: x.get("city") or x.get("station") or "")
    return result


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            station = params.get("station", [None])[0]
            city = _safe_city(params.get("city", [None])[0])
            hours = _safe_int(params.get("hours", ["24"])[0], 24, 1, 168)
            limit = _safe_int(params.get("limit", ["500"])[0], 500, 1, 1000)
            summary = params.get("summary", ["false"])[0].lower() == "true"

            # Time filter — use simple format without timezone offset for Supabase
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Build query
            query = f"metar_readings?select=*&order=polled_at.desc&limit={limit}"
            query += f"&polled_at=gte.{cutoff}"

            if station:
                query += f"&station=eq.{quote(station)}"
            if city:
                query += f"&city=eq.{quote(city)}"

            data = supabase_query(query)

            if summary:
                data = build_summary(data)

            self._respond(200, data)

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
