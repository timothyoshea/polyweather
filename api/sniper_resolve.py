"""
Vercel Python serverless function — resolves open sniper trades via Polymarket Gamma API.

POST /api/sniper_resolve    — check all open trades and resolve won/lost ones
GET  /api/sniper_resolve    — same (for Vercel cron compatibility)
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
import urllib.request
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone

from auth_helper import require_auth

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

GAMMA_API = "https://gamma-api.polymarket.com/markets"


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


def supabase_update(table, row_id, data):
    """PATCH a row in Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{row_id}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def fetch_gamma_market(market_id):
    """Fetch market data from Gamma API by event ID or market ID."""
    # Try as event first (sniper stores event IDs as market_id)
    for endpoint in [f"events?id={market_id}", f"markets?id={market_id}"]:
        try:
            url = f"https://gamma-api.polymarket.com/{endpoint}"
            req = urllib.request.Request(url, headers={"User-Agent": "PolyWeather/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data:
                    return data[0] if isinstance(data, list) else data
        except Exception:
            continue
    return None


def resolve_trades():
    """Check open trades and resolve any that have settled."""
    open_trades = supabase_query("sniper_trades?select=*&status=eq.open")
    results = {"checked": 0, "resolved": 0, "errors": [], "details": []}

    for trade in open_trades:
        results["checked"] += 1
        market_id = trade.get("market_id")
        if not market_id:
            results["errors"].append(f"Trade {trade['id']}: no market_id")
            continue

        try:
            try:
                market = fetch_gamma_market(market_id)
            except Exception:
                market = None  # Gamma unavailable, fall through to fallback

            # Check if market has resolved via Gamma
            resolved_via_gamma = False
            if market:
                closed = market.get("closed", False)
                auto_resolved = market.get("automaticallyResolved", False)
                resolved_via_gamma = closed or auto_resolved

            # Fallback: if market not found or not closed, check if date has passed
            # and resolve using the METAR temp we recorded
            market_date = trade.get("market_date", "")
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            if not resolved_via_gamma:
                if market_date and market_date < today:
                    # Past-date trade — resolve from recorded temp
                    temp = trade.get("temp_observed")
                    band_temp = trade.get("band_temp")
                    side = trade.get("side", "")
                    trade_type = trade.get("trade_type", "")

                    if temp is not None and band_temp is not None:
                        if trade_type == "top_band_yes":
                            our_side_won = temp > band_temp
                        else:  # lower_band_no
                            our_side_won = temp > band_temp  # NO wins when temp exceeded the band

                        size_usdc = float(trade.get("size_usdc") or 0)
                        total_shares = float(trade.get("total_shares") or 0)
                        entry_price = float(trade.get("entry_price") or 0)

                        if our_side_won:
                            profit = round(total_shares - size_usdc, 4) if total_shares else 0
                            new_status = "won"
                        else:
                            profit = round(-size_usdc, 4)
                            new_status = "lost"

                        supabase_update("sniper_trades", trade["id"], {
                            "status": new_status,
                            "profit_usd": profit,
                            "resolved_at": datetime.now(timezone.utc).isoformat(),
                        })
                        results["resolved"] += 1
                        results["details"].append(
                            f"{trade.get('city')} {trade.get('band_label')} {side}: "
                            f"{new_status} (fallback, temp={temp}, profit=${profit})"
                        )
                        continue

                continue  # Market still open or can't resolve

            # Determine outcome: check prices to see which side won
            # outcomePrices is a JSON string like "[\"0.95\",\"0.05\"]"
            outcome_prices_raw = market.get("outcomePrices", "")
            if isinstance(outcome_prices_raw, str):
                try:
                    outcome_prices = json.loads(outcome_prices_raw)
                except (json.JSONDecodeError, TypeError):
                    outcome_prices = []
            else:
                outcome_prices = outcome_prices_raw or []

            # outcomes[0] = YES price, outcomes[1] = NO price
            yes_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0
            no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0

            side = trade["side"]
            size_usdc = float(trade.get("size_usdc") or 50)
            total_shares = float(trade.get("total_shares") or 0)

            # Did our side win?
            if side == "YES":
                our_side_won = yes_price > 0.95
            else:
                our_side_won = no_price > 0.95

            if our_side_won:
                # Shares redeem at $1 each
                profit = round(total_shares - size_usdc, 4) if total_shares else 0
                new_status = "won"
            else:
                profit = round(-size_usdc, 4)
                new_status = "lost"

            now = datetime.now(timezone.utc).isoformat()
            supabase_update("sniper_trades", trade["id"], {
                "status": new_status,
                "profit_usd": profit,
                "resolved_at": now,
            })

            results["resolved"] += 1
            results["details"].append({
                "trade_id": trade["id"],
                "band_label": trade.get("band_label"),
                "side": side,
                "status": new_status,
                "profit_usd": profit,
            })

        except Exception as e:
            results["errors"].append(f"Trade {trade['id']}: {str(e)}")

    return results


class handler(BaseHTTPRequestHandler):
    def _run_resolve(self):
        try:
            data = resolve_trades()
            self._respond(200, data)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_POST(self):
        if not require_auth(self):
            return
        self._run_resolve()

    def do_GET(self):
        if not require_auth(self):
            return
        self._run_resolve()

    def _respond(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self._respond(200, {})
