"""
Vercel Python serverless function — tracks capital gap (missed trading opportunity)
due to capital constraints in the Safe NO Volume Live portfolio.

GET /api/capital_gap?portfolio_id=xxx          — recent snapshots
GET /api/capital_gap?portfolio_id=xxx&summary=true  — aggregated stats
GET /api/capital_gap?portfolio_id=xxx&limit=50 — limit results
GET /api/capital_gap?portfolio_id=xxx&from=2026-04-01&to=2026-04-16 — date range
POST /api/capital_gap  {portfolio_id: "..."}   — run shadow evaluator, log snapshot
"""
import os
import json
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, date
from collections import defaultdict

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

MIN_EDGE_PP = 3.0
FEE_RATE = 0.0125


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def supabase_query(path, use_service_key=False):
    """Query Supabase REST API (GET)."""
    key = SUPABASE_SERVICE_KEY if use_service_key else SUPABASE_ANON_KEY
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def supabase_insert(table, row):
    """Insert a single row into a Supabase table using the SERVICE KEY.
    Returns the inserted row (Prefer: return=representation).
    """
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    data = json.dumps([row]).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Strategy filter (ported from trading_loop.py)
# ---------------------------------------------------------------------------

def passes_strategy_filters(opp, strategy):
    """Return True if the opportunity passes all strategy filters."""
    allowed_sides = strategy.get("allowed_sides")
    allowed_bet_types = strategy.get("allowed_bet_types")
    allowed_band_types = strategy.get("allowed_band_types")
    blocked_cities = strategy.get("blocked_cities", [])
    allowed_cities = strategy.get("allowed_cities", [])

    if allowed_sides and opp.get("side", "") not in allowed_sides:
        return False
    if allowed_bet_types and opp.get("bet_type", "") not in allowed_bet_types:
        return False
    if allowed_band_types and opp.get("band_type", "") not in allowed_band_types:
        return False
    if blocked_cities and opp.get("city", "") in blocked_cities:
        return False
    if allowed_cities and opp.get("city", "") not in allowed_cities:
        return False

    # Entry price filter
    min_entry = strategy.get("preferred_entry_price_min")
    if min_entry is not None:
        entry_price = opp.get("entry_price") or opp.get("mkt_p", 0) / 100
        if entry_price and float(entry_price) < float(min_entry):
            return False

    # Bet-type specific confidence / edge caps
    opp_conf = opp.get("confidence", 0) or 0
    opp_bt = opp.get("bet_type", "")
    if opp_bt == "edge":
        max_conf = strategy.get("edge_bet", {}).get("max_confidence")
        if max_conf is not None and opp_conf > float(max_conf):
            return False
        max_edge = strategy.get("edge_bet", {}).get("max_edge")
        opp_edge = opp.get("edge", 0) or 0
        if max_edge is not None and opp_edge > float(max_edge) * 100:
            return False
    elif opp_bt == "safe_no":
        max_conf = strategy.get("safe_no", {}).get("max_confidence")
        if max_conf is not None and opp_conf > float(max_conf):
            return False

    # Ensemble std filter
    fd = opp.get("forecast_details") or {}
    ens_std = fd.get("ensemble_std")
    ens_std_min = strategy.get("ensemble_std_min")
    if ens_std_min is not None and ens_std is not None:
        if float(ens_std) < float(ens_std_min):
            return False
    ens_std_max = strategy.get("ensemble_std_max")
    if ens_std_max is not None and ens_std is not None:
        if float(ens_std) > float(ens_std_max):
            return False

    return True


# ---------------------------------------------------------------------------
# Position sizing (ported from trading_loop.py)
# ---------------------------------------------------------------------------

def compute_position(liquidity):
    """Compute VWAP position from positive-edge book levels.

    Returns dict with entry_price, total_cost_usd, total_shares, num_levels
    or None if no viable position.
    """
    if not liquidity or not isinstance(liquidity, dict):
        return None
    book_levels = liquidity.get("book_levels", [])
    if not book_levels:
        return None
    positive_levels = [lv for lv in book_levels if lv.get("edge_pp", 0) > 0]
    if not positive_levels:
        return None
    total_cost = 0.0
    total_shares = 0.0
    for lv in positive_levels:
        cost = lv.get("cost_usd", 0)
        shares = lv.get("shares", 0)
        if shares > 0 and cost > 0:
            total_cost += cost
            total_shares += shares
    if total_shares <= 0 or total_cost <= 0:
        return None
    return {
        "entry_price": round(total_cost / total_shares, 6),
        "total_cost_usd": round(total_cost, 2),
        "total_shares": round(total_shares, 2),
        "num_levels": len(positive_levels),
    }


# ---------------------------------------------------------------------------
# Shadow evaluator — core logic
# ---------------------------------------------------------------------------

def run_shadow_evaluation(portfolio_id):
    """Evaluate all current opportunities against a portfolio's strategy.

    Returns a snapshot dict suitable for inserting into capital_gap_log.
    """
    # 1. Fetch portfolio
    portfolios = supabase_query(f"portfolios?id=eq.{portfolio_id}&select=*")
    if not portfolios:
        raise ValueError(f"Portfolio {portfolio_id} not found")
    portfolio = portfolios[0]

    strategy = portfolio.get("strategy") or {}
    starting_capital = float(portfolio.get("starting_capital_usd", 0) or 0)

    # 2. Fetch opportunities (date >= today, UTC)
    today_str = date.today().isoformat()
    opps_raw = supabase_query(
        "opportunities?select=*&order=created_at.desc&limit=500"
    )
    opps = [o for o in opps_raw if (o.get("date") or "") >= today_str]
    total_opps = len(opps)

    # 3. Fetch open trades for deployed capital + city exposure
    open_trades = supabase_query(
        f"paper_trades?status=eq.open&portfolio_id=eq.{portfolio_id}"
        f"&select=total_cost_usd,city"
    )
    deployed_usd = sum(float(t.get("total_cost_usd", 0) or 0) for t in open_trades)
    city_exposure = defaultdict(float)
    for t in open_trades:
        city_exposure[t.get("city", "")] += float(t.get("total_cost_usd", 0) or 0)

    # 4. Fetch realized P&L to compute current capital
    resolved_trades = supabase_query(
        f"paper_trades?status=in.(won,lost)&portfolio_id=eq.{portfolio_id}"
        f"&select=profit_usd"
    )
    realized_pnl = sum(float(t.get("profit_usd", 0) or 0) for t in resolved_trades)
    capital_usd = starting_capital + realized_pnl
    available_usd = max(capital_usd - deployed_usd, 0.0)
    utilization_pct = (deployed_usd / capital_usd * 100) if capital_usd > 0 else 0.0

    # 5. Capital management thresholds
    cap_mgmt = strategy.get("capital_management", {})
    max_single_trade_usd = float(cap_mgmt.get("max_single_trade_usd", 999999))
    max_single_trade_pct = float(cap_mgmt.get("max_single_trade_pct", 100))
    max_portfolio_util_pct = float(cap_mgmt.get("max_portfolio_utilization_pct", 100))
    max_corr_exposure_pct = float(cap_mgmt.get("max_correlated_exposure_pct", 100))

    # Track positions we've "virtually accepted" to simulate sequential allocation
    virtual_deployed = deployed_usd
    virtual_city_exposure = dict(city_exposure)

    # Counters
    passed_filters = 0
    with_edge = 0
    ready = 0
    blocked_capital = 0
    blocked_city_exposure_count = 0
    blocked_duplicate = 0

    missed_cost_usd = 0.0
    missed_profit_usd = 0.0
    extra_capital_needed_usd = 0.0
    details = []

    # Track seen opportunities to avoid double-counting same market
    seen_keys = set()

    for opp in opps:
        # Dedup: same city+date+band+side is one market
        opp_key = (
            opp.get("city", ""),
            opp.get("date", ""),
            opp.get("band_c", ""),
            opp.get("side", ""),
        )
        if opp_key in seen_keys:
            blocked_duplicate += 1
            continue

        # 5a. Strategy filters
        if not passes_strategy_filters(opp, strategy):
            continue
        passed_filters += 1

        # 5b. Edge check
        my_p = float(opp.get("my_p", 0) or 0)
        mkt_p = float(opp.get("mkt_p", 0) or 0)
        edge_pp = my_p - mkt_p
        if edge_pp < MIN_EDGE_PP:
            continue
        with_edge += 1

        # 5c. Position sizing
        liquidity = opp.get("liquidity")
        if isinstance(liquidity, str):
            try:
                liquidity = json.loads(liquidity)
            except Exception:
                liquidity = None

        position = compute_position(liquidity)
        if not position:
            continue
        ready += 1

        raw_cost = position["total_cost_usd"]
        total_shares = position["total_shares"]

        # Cap trade size: min(raw_cost, max_single_trade_usd, capital * max_pct)
        size_cap_usd = min(
            raw_cost,
            max_single_trade_usd,
            capital_usd * max_single_trade_pct / 100,
        )
        cost_with_fees = size_cap_usd * (1 + FEE_RATE)

        # Scale shares proportionally if size was capped
        scale = size_cap_usd / raw_cost if raw_cost > 0 else 1.0
        est_shares = total_shares * scale
        est_profit = est_shares - size_cap_usd  # each share pays $1 if won

        city = opp.get("city", "")

        # 5d. Check portfolio utilization cap
        util_limit = capital_usd * max_portfolio_util_pct / 100
        if virtual_deployed + cost_with_fees > util_limit:
            blocked_capital += 1
            missed_cost_usd += cost_with_fees
            missed_profit_usd += est_profit
            extra_capital_needed_usd += cost_with_fees
            details.append({
                "city": city,
                "date": opp.get("date"),
                "band_c": opp.get("band_c"),
                "side": opp.get("side"),
                "bet_type": opp.get("bet_type"),
                "cost": round(cost_with_fees, 2),
                "est_profit": round(est_profit, 2),
                "edge_pp": round(edge_pp, 2),
                "blocked_reason": "util",
            })
            seen_keys.add(opp_key)
            continue

        # 5e. Check city/correlated exposure cap
        city_limit = capital_usd * max_corr_exposure_pct / 100
        city_exp_now = virtual_city_exposure.get(city, 0.0)
        if city_exp_now + cost_with_fees > city_limit:
            blocked_city_exposure_count += 1
            missed_cost_usd += cost_with_fees
            missed_profit_usd += est_profit
            extra_capital_needed_usd += cost_with_fees
            details.append({
                "city": city,
                "date": opp.get("date"),
                "band_c": opp.get("band_c"),
                "side": opp.get("side"),
                "bet_type": opp.get("bet_type"),
                "cost": round(cost_with_fees, 2),
                "est_profit": round(est_profit, 2),
                "edge_pp": round(edge_pp, 2),
                "blocked_reason": "city",
            })
            seen_keys.add(opp_key)
            continue

        # Trade passes — virtually allocate it
        virtual_deployed += cost_with_fees
        virtual_city_exposure[city] = city_exp_now + cost_with_fees
        seen_keys.add(opp_key)

    snapshot = {
        "portfolio_id": portfolio_id,
        "total_opps": total_opps,
        "passed_filters": passed_filters,
        "with_edge": with_edge,
        "ready": ready,
        "blocked_capital": blocked_capital,
        "blocked_city_exposure": blocked_city_exposure_count,
        "blocked_duplicate": blocked_duplicate,
        "missed_cost_usd": round(missed_cost_usd, 2),
        "missed_profit_usd": round(missed_profit_usd, 2),
        "deployed_usd": round(deployed_usd, 2),
        "available_usd": round(available_usd, 2),
        "capital_usd": round(capital_usd, 2),
        "utilization_pct": round(utilization_pct, 2),
        "extra_capital_needed_usd": round(extra_capital_needed_usd, 2),
        "details": details,
    }
    return snapshot


# ---------------------------------------------------------------------------
# Summary builder for GET ?summary=true
# ---------------------------------------------------------------------------

def build_summary(rows):
    """Aggregate a list of capital_gap_log rows into summary stats."""
    if not rows:
        return {
            "snapshot_count": 0,
            "total_missed_profit_usd": 0.0,
            "total_missed_cost_usd": 0.0,
            "avg_blocked_per_snapshot": 0.0,
            "avg_missed_profit_per_snapshot": 0.0,
            "avg_utilization_pct": 0.0,
            "projected_daily_missed_profit_usd": 0.0,
            "projected_weekly_missed_profit_usd": 0.0,
            "by_day": [],
        }

    n = len(rows)
    total_missed_profit = sum(float(r.get("missed_profit_usd", 0) or 0) for r in rows)
    total_missed_cost = sum(float(r.get("missed_cost_usd", 0) or 0) for r in rows)
    avg_blocked = sum(
        (r.get("blocked_capital", 0) or 0) + (r.get("blocked_city_exposure", 0) or 0)
        for r in rows
    ) / n
    avg_missed_profit = total_missed_profit / n
    avg_util = sum(float(r.get("utilization_pct", 0) or 0) for r in rows) / n

    # Snapshots per day estimate: ~6 per hour * 24 = 144 if every 10 min; use actual data
    by_day = defaultdict(lambda: {"snapshot_count": 0, "missed_profit": 0.0, "missed_cost": 0.0, "blocked": 0})
    for r in rows:
        ts = r.get("created_at", "")
        day = ts[:10] if ts else "unknown"
        by_day[day]["snapshot_count"] += 1
        by_day[day]["missed_profit"] += float(r.get("missed_profit_usd", 0) or 0)
        by_day[day]["missed_cost"] += float(r.get("missed_cost_usd", 0) or 0)
        by_day[day]["blocked"] += (
            (r.get("blocked_capital", 0) or 0) + (r.get("blocked_city_exposure", 0) or 0)
        )

    by_day_list = []
    for day in sorted(by_day.keys()):
        d = by_day[day]
        by_day_list.append({
            "date": day,
            "snapshot_count": d["snapshot_count"],
            "missed_profit_usd": round(d["missed_profit"], 2),
            "missed_cost_usd": round(d["missed_cost"], 2),
            "blocked_trades": d["blocked"],
        })

    # Project daily missed profit from recent snapshots (last 7 days of data)
    recent_days = [d for d in by_day_list if d["date"] != "unknown"][-7:]
    if recent_days:
        daily_avg = sum(d["missed_profit_usd"] for d in recent_days) / len(recent_days)
    else:
        daily_avg = avg_missed_profit * 6  # fallback: ~6 snapshots/hour * rough daily

    return {
        "snapshot_count": n,
        "total_missed_profit_usd": round(total_missed_profit, 2),
        "total_missed_cost_usd": round(total_missed_cost, 2),
        "avg_blocked_per_snapshot": round(avg_blocked, 2),
        "avg_missed_profit_per_snapshot": round(avg_missed_profit, 2),
        "avg_utilization_pct": round(avg_util, 2),
        "projected_daily_missed_profit_usd": round(daily_avg, 2),
        "projected_weekly_missed_profit_usd": round(daily_avg * 7, 2),
        "by_day": by_day_list,
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            portfolio_id = params.get("portfolio_id", [None])[0]
            if not portfolio_id:
                self._respond(400, {"error": "portfolio_id is required"})
                return

            limit = int(params.get("limit", ["100"])[0])
            from_date = params.get("from", [None])[0]
            to_date = params.get("to", [None])[0]
            is_summary = params.get("summary", [None])[0] == "true"

            query = (
                f"capital_gap_log?portfolio_id=eq.{urllib.parse.quote(portfolio_id)}"
                f"&order=created_at.desc"
            )
            if from_date:
                query += f"&created_at=gte.{urllib.parse.quote(from_date)}"
            if to_date:
                query += f"&created_at=lte.{urllib.parse.quote(to_date)}T23:59:59Z"
            if not is_summary:
                query += f"&limit={limit}"

            rows = supabase_query(query)

            if is_summary:
                self._respond(200, build_summary(rows))
            else:
                self._respond(200, rows)

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload = json.loads(body.decode("utf-8"))

            portfolio_id = payload.get("portfolio_id")
            if not portfolio_id:
                self._respond(400, {"error": "portfolio_id is required"})
                return

            snapshot = run_shadow_evaluation(portfolio_id)
            inserted = supabase_insert("capital_gap_log", snapshot)

            result = inserted[0] if inserted else snapshot
            self._respond(200, result)

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def _respond(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
