"""
Vercel Python serverless function — compares original trade forecasts with latest scanner data.

GET /api/forecast_compare?portfolio_id=xxx

For each open trade, finds the latest matching opportunity from the opportunities table
and returns both original and latest forecast data with a recommendation.
"""
import os
import json
import re
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


def supabase_get(path):
    """Query Supabase REST API with service key."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_band_threshold(band_c):
    """Extract the threshold temperature from a band string like '26°C', '>=29°C', '16-16°C'."""
    if not band_c:
        return None
    # ">=29°C" or "≥29°C"
    m = re.search(r'[>≥]=?\s*(-?\d+)', band_c)
    if m:
        return float(m.group(1))
    # "<=8°C" or "≤8°C"
    m = re.search(r'[<≤]=?\s*(-?\d+)', band_c)
    if m:
        return float(m.group(1))
    # "26°C" or "26-26°C" or "16-16°C"
    m = re.search(r'(-?\d+)', band_c)
    if m:
        return float(m.group(1))
    return None


def get_recommendation(trade, latest_scan, live_price=None):
    """Determine recommendation based on forecast distance from band threshold.

    live_price: current market price as 0-1 float (e.g. 0.96). If provided,
    used to calculate captured_pct accurately.
    """
    if latest_scan is None:
        return "hold", None, 0

    # Parse band threshold
    band = trade.get("band_c", "")
    threshold = parse_band_threshold(band)
    if threshold is None:
        return "hold", None, 0

    # Get latest forecast
    latest_fc = float(latest_scan.get("forecast_c") or trade.get("forecast_c") or 0)
    side = trade.get("side", "").upper()
    band_type = trade.get("band_type", "exact")

    # Calculate forecast gap from band
    if side == "NO":
        if band_type == "above":  # >=X, NO wins if temp < X
            gap = threshold - latest_fc
        elif band_type == "below":  # <=X, NO wins if temp > X
            gap = latest_fc - threshold
        else:  # exact band, NO wins if temp not in band
            gap = abs(latest_fc - threshold)
    else:  # YES
        if band_type == "above":
            gap = latest_fc - threshold
        elif band_type == "below":
            gap = threshold - latest_fc
        else:
            gap = -abs(latest_fc - threshold)  # negative = outside band

    # Calculate captured upside from live price
    shares = float(trade.get("total_shares", 0) or 0)
    cost = float(trade.get("total_cost_usd", 0) or 0)
    max_profit = shares - cost
    if live_price is not None:
        unrealized = shares * float(live_price) - cost
    else:
        unrealized = float(trade.get("unrealized_pnl", 0) or 0)
    captured_pct = (unrealized / max_profit * 100) if max_profit > 0 else 0

    # ── Recommendation matrix ──

    # 1. High capture — take profit regardless of gap (capital redeployment)
    if captured_pct >= 90:
        return "take_profit", gap, captured_pct

    # 2. Thesis broken — forecast crossed band
    if gap < 0:
        return "exit_forecast_changed", gap, captured_pct

    # 3. Danger zone — very close to band
    if gap < 1.0:
        if captured_pct > 50:
            return "exit_forecast_changed", gap, captured_pct
        return "danger", gap, captured_pct

    # 4. Watch zone
    if gap < 3.0:
        if captured_pct > 80:
            return "take_profit", gap, captured_pct
        return "hold", gap, captured_pct

    # 5. Safe zone (gap >= 3°C)
    if captured_pct > 80:
        return "take_profit", gap, captured_pct
    if captured_pct > 50:
        return "consider_exit", gap, captured_pct
    return "hold", gap, captured_pct


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            portfolio_id = params.get("portfolio_id", [None])[0]

            if not portfolio_id:
                self._respond(400, {"error": "portfolio_id is required"})
                return

            # 1. Fetch all open trades for the portfolio
            trades = supabase_get(
                f"paper_trades?status=eq.open&portfolio_id=eq.{portfolio_id}"
                f"&select=*&order=created_at.desc"
            )

            if not trades:
                self._respond(200, {"comparisons": []})
                return

            # 2. Collect unique market keys (city+date+band_c+side) and fetch
            #    latest opportunity for each. We batch by fetching recent opps
            #    for relevant cities/dates and then matching in Python.
            cities = set()
            dates = set()
            for t in trades:
                if t.get("city"):
                    cities.add(t["city"])
                if t.get("date"):
                    dates.add(t["date"])

            # Fetch recent opportunities matching any of these cities and dates
            # Use OR filters via Supabase
            all_opps = []
            for city in cities:
                city_encoded = quote(city, safe="")
                date_list = ",".join(f'"{d}"' for d in sorted(dates))
                try:
                    opps = supabase_get(
                        f"opportunities?city=eq.{city_encoded}"
                        f"&date=in.({date_list})"
                        f"&order=created_at.desc"
                        f"&limit=500"
                    )
                    all_opps.extend(opps)
                except Exception as e:
                    print(f"[WARN] Failed to fetch opps for {city}: {e}")

            # 3. Index opportunities by (city, date, band_c, side) — keep only latest
            opp_index = {}
            for opp in all_opps:
                key = (
                    opp.get("city", ""),
                    opp.get("date", ""),
                    opp.get("band_c", ""),
                    opp.get("side", ""),
                )
                # Since ordered by created_at desc, first seen is latest
                if key not in opp_index:
                    opp_index[key] = opp

            # 4. Fetch live midpoints for all trades' token_ids
            live_prices = {}  # token_id -> float (0-1)
            token_ids = [t.get("token_id") for t in trades if t.get("token_id")]
            for tid in token_ids:
                try:
                    mid_url = f"https://clob.polymarket.com/midpoint?token_id={quote(tid, safe='')}"
                    req = urllib.request.Request(mid_url, headers={"User-Agent": "PolyWeather/1.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        mid_data = json.loads(resp.read().decode("utf-8"))
                        mid = mid_data.get("mid")
                        if mid is not None:
                            live_prices[tid] = float(mid)
                except Exception:
                    pass

            # 5. Build comparisons
            comparisons = []
            for t in trades:
                key = (
                    t.get("city", ""),
                    t.get("date", ""),
                    t.get("band_c", ""),
                    t.get("side", ""),
                )
                latest = opp_index.get(key)

                latest_scan = None
                if latest:
                    latest_scan = {
                        "forecast_c": latest.get("forecast_c"),
                        "my_p": latest.get("my_p"),
                        "edge": latest.get("edge"),
                        "confidence": latest.get("confidence"),
                        "mkt_p": latest.get("mkt_p"),
                        "created_at": latest.get("created_at"),
                    }

                orig_edge = float(t.get("edge") or 0)
                latest_edge = float(latest.get("edge") or 0) if latest else None

                forecast_changed = False
                edge_improved = False
                if latest:
                    orig_fc = t.get("forecast_c")
                    latest_fc = latest.get("forecast_c")
                    if orig_fc is not None and latest_fc is not None:
                        forecast_changed = abs(float(orig_fc) - float(latest_fc)) > 0.5
                    if latest_edge is not None and orig_edge != 0:
                        edge_improved = abs(latest_edge) > abs(orig_edge)

                live_price = live_prices.get(t.get("token_id"))
                rec, gap, captured_pct = get_recommendation(t, latest_scan, live_price)

                comparisons.append({
                    "trade": {
                        "id": t.get("id"),
                        "city": t.get("city"),
                        "date": t.get("date"),
                        "band_c": t.get("band_c"),
                        "band_f": t.get("band_f"),
                        "side": t.get("side"),
                        "bet_type": t.get("bet_type"),
                        "forecast_c": t.get("forecast_c"),
                        "my_p": t.get("my_p"),
                        "edge": t.get("edge"),
                        "confidence": t.get("confidence"),
                        "entry_price": t.get("entry_price"),
                        "total_cost_usd": t.get("total_cost_usd"),
                        "created_at": t.get("created_at"),
                        "live_price": t.get("live_price"),
                        "unrealized_pnl": t.get("unrealized_pnl"),
                        "unrealized_pnl_pct": t.get("unrealized_pnl_pct"),
                    },
                    "latest_scan": latest_scan,
                    "forecast_changed": forecast_changed,
                    "edge_improved": edge_improved,
                    "recommendation": rec,
                    "forecast_gap": round(gap, 1) if gap is not None else None,
                    "captured_pct": round(captured_pct, 1),
                })

            self._respond(200, {"comparisons": comparisons})

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._respond(500, {"error": str(e)})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _respond(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
