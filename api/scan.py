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
config.DEBUG = False

from scanner import scan
from output import polymarket_url
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


def run_scan_and_save(mode="all"):
    """Run the scanner and persist results to Supabase."""
    # Set mode per-request (module-level config is shared across warm invocations)
    config.TOMORROW = (mode == "tomorrow")
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
        "mode": mode,
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
                    **{k: o.get(k) for k in [
                        "combined_forecast", "ensemble_mean", "ensemble_std",
                        "ensemble_min", "ensemble_max", "multi_model_spread",
                        "eff_std", "horizon_days", "city_tier",
                    ] if o.get(k) is not None},
                    "model_weights": o.get("model_weights", {}),
                },
            })
        supabase_insert("opportunities", opp_rows)

        # Open paper trades for each active portfolio
        try:
            from paper_trading import open_paper_trades
            import urllib.request as ur
            import copy

            # Fetch all active portfolios
            pf_url = f"{SUPABASE_URL}/rest/v1/portfolios?active=eq.true&select=*"
            pf_headers = {
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            }
            pf_req = ur.Request(pf_url, headers=pf_headers, method="GET")
            with ur.urlopen(pf_req, timeout=10) as pf_resp:
                portfolios = json.loads(pf_resp.read().decode("utf-8"))

            if portfolios:
                from live_trading import execute_live_trades

                for pf in portfolios:
                    try:
                        pf_opps = copy.deepcopy(opps)
                        trade_mode = pf.get("trade_mode", "paper")

                        if trade_mode == "live":
                            execute_live_trades(
                                pf_opps, scan_id, SUPABASE_URL, SUPABASE_SERVICE_KEY,
                                portfolio_id=pf["id"], portfolio=pf
                            )
                            print(f"[INFO] Live trades for portfolio: {pf.get('name', pf['id'])}")
                        else:
                            open_paper_trades(
                                pf_opps, scan_id, SUPABASE_URL, SUPABASE_SERVICE_KEY,
                                portfolio_id=pf["id"], portfolio=pf
                            )
                            print(f"[INFO] Paper trades for portfolio: {pf.get('name', pf['id'])}")
                    except Exception as pf_err:
                        print(f"[WARN] Trading error for {pf.get('name')}: {pf_err}")
            else:
                # Fallback: no portfolios, open without portfolio context
                open_paper_trades(opps, scan_id, SUPABASE_URL, SUPABASE_SERVICE_KEY)
        except Exception as e:
            print(f"[WARN] Paper trading error: {e}")

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
        if not require_auth(self):
            return
        try:
            if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": "SUPABASE_URL and SUPABASE_SERVICE_KEY env vars required"
                }).encode())
                return

            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(self.path).query)
            mode = params.get("mode", ["all"])[0]
            result = run_scan_and_save(mode)

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
