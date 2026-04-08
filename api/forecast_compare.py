"""
Vercel Python serverless function — compares original trade forecasts with latest scanner data.

GET /api/forecast_compare?portfolio_id=xxx

For each open trade, finds the latest matching opportunity from the opportunities table
and returns both original and latest forecast data with a recommendation.
"""
import os
import json
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


def get_recommendation(trade, latest_scan):
    """Determine recommendation based on original vs latest forecast."""
    if latest_scan is None:
        return "hold"

    orig_edge = float(trade.get("edge") or 0)
    orig_my_p = float(trade.get("my_p") or 0)
    latest_edge = float(latest_scan.get("edge") or 0)
    latest_my_p = float(latest_scan.get("my_p") or 0)

    # Forecast flipped direction (was >50% now <50% or vice versa)
    if orig_my_p > 0.5 and latest_my_p < 0.5:
        return "exit_forecast_changed"
    if orig_my_p < 0.5 and latest_my_p > 0.5:
        return "exit_forecast_changed"

    # Edge halved or worse
    if orig_edge != 0 and abs(latest_edge) < abs(orig_edge) * 0.5:
        return "exit_forecast_changed"

    # Edge improved 20%+
    if orig_edge != 0 and abs(latest_edge) > abs(orig_edge) * 1.2:
        return "double_down"

    # Profitable with time remaining — could free capital
    pnl_pct = float(trade.get("unrealized_pnl_pct") or 0)
    if pnl_pct > 3:
        return "consider_exit"

    return "hold"


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

            # 4. Build comparisons
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

                rec = get_recommendation(t, latest_scan)

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
