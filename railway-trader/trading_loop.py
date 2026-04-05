"""
PolyWeather Trading Loop — Self-contained continuous trader for Railway.

Replaces the Vercel->Railway HTTP hop by running a background loop that:
1. Fetches active live portfolios from Supabase
2. Caches scanner opportunities from Supabase
3. Polls Polymarket prices every few seconds
4. Executes trades locally when edges are detected

Runs as a daemon thread alongside the Flask app.
"""
import os
import json
import time
import threading
import traceback
import urllib.request
import urllib.error
from datetime import datetime, timezone, date

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds, OrderArgs, OrderType,
    BalanceAllowanceParams, AssetType,
)
from py_clob_client.constants import POLYGON
from py_clob_client.order_builder.constants import BUY

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

POLYMARKET_PRIVATE_KEY = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
CLOB_API_KEY = os.environ.get("CLOB_API_KEY", "")
CLOB_API_SECRET = os.environ.get("CLOB_API_SECRET", "")
CLOB_API_PASSPHRASE = os.environ.get("CLOB_API_PASSPHRASE", "")

POLYMARKET_FEE_RATE = 0.0125

# Intervals (seconds)
PORTFOLIO_REFRESH_INTERVAL = 60
OPP_REFRESH_INTERVAL = 60
PRICE_POLL_INTERVAL = 3

# Minimum edge (percentage points) to trigger a trade
MIN_EDGE_PP = 3.0

CLOB_HOST = "https://clob.polymarket.com"


def _log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[LOOP {ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# HTTP helpers (no external deps beyond stdlib + py_clob_client)
# ---------------------------------------------------------------------------

def _http_get(url, headers=None, timeout=15):
    """GET request returning parsed JSON."""
    hdrs = headers or {}
    hdrs.setdefault("User-Agent", "PolyWeather/1.0")
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post(url, data, headers=None, timeout=15):
    """POST request returning parsed JSON."""
    encoded = json.dumps(data).encode("utf-8") if data is not None else None
    hdrs = headers or {}
    hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=encoded, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body.strip() else {}


def _http_patch(url, data, headers=None, timeout=15):
    """PATCH request returning parsed JSON."""
    encoded = json.dumps(data).encode("utf-8") if data is not None else None
    hdrs = headers or {}
    hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=encoded, headers=hdrs, method="PATCH")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body.strip() else {}


def _supabase_headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


# ---------------------------------------------------------------------------
# Trading hours check (mirrors paper_trading.py / live_trading.py)
# ---------------------------------------------------------------------------

def _check_trading_hours(strategy):
    """Check if current UTC time is within allowed trading hours.

    Returns (allowed: bool, reason: str).
    """
    trading_hours = strategy.get("trading_hours")
    if not trading_hours or not trading_hours.get("enabled", False):
        return True, "no restrictions"

    now = datetime.now(timezone.utc)
    current_minutes = now.hour * 60 + now.minute
    current_time_str = now.strftime("%H:%M")

    def _parse_time(t):
        parts = t.strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])

    def _in_window(start_str, end_str):
        start = _parse_time(start_str)
        end = _parse_time(end_str)
        if start <= end:
            return start <= current_minutes < end
        else:
            return current_minutes >= start or current_minutes < end

    # Blackout takes priority
    for bw in trading_hours.get("blackout_windows", []):
        if _in_window(bw.get("start", "00:00"), bw.get("end", "00:00")):
            return False, f"blackout {bw['start']}-{bw['end']} (now={current_time_str} UTC)"

    allowed_windows = trading_hours.get("allowed_windows", [])
    if allowed_windows:
        for aw in allowed_windows:
            if _in_window(aw.get("start", "00:00"), aw.get("end", "23:59")):
                return True, f"allowed {aw['start']}-{aw['end']}"
        return False, f"outside allowed windows (now={current_time_str} UTC)"

    return True, "no restrictions"


# ---------------------------------------------------------------------------
# Strategy filters (mirrors paper_trading.py / live_trading.py)
# ---------------------------------------------------------------------------

def _passes_strategy_filters(opp, strategy):
    """Return (passes: bool, reason: str) for a single opportunity."""
    allowed_sides = strategy.get("allowed_sides")
    allowed_bet_types = strategy.get("allowed_bet_types")
    allowed_band_types = strategy.get("allowed_band_types")
    blocked_cities = strategy.get("blocked_cities", [])
    allowed_cities = strategy.get("allowed_cities", [])

    opp_side = opp.get("side", "")
    opp_bet_type = opp.get("bet_type", "")
    opp_band_type = opp.get("band_type", "")
    opp_city = opp.get("city", "")

    if allowed_sides and opp_side not in allowed_sides:
        return False, f"side {opp_side} not in {allowed_sides}"
    if allowed_bet_types and opp_bet_type not in allowed_bet_types:
        return False, f"bet_type {opp_bet_type} not in {allowed_bet_types}"
    if allowed_band_types and opp_band_type not in allowed_band_types:
        return False, f"band_type {opp_band_type} not in {allowed_band_types}"
    if blocked_cities and opp_city in blocked_cities:
        return False, f"city {opp_city} blocked"
    if allowed_cities and opp_city not in allowed_cities:
        return False, f"city {opp_city} not in allowed_cities"

    # Entry price filter
    min_entry = strategy.get("preferred_entry_price_min")
    if min_entry is not None:
        entry_price = opp.get("entry_price") or opp.get("mkt_p", 0) / 100
        if entry_price and float(entry_price) < float(min_entry):
            return False, f"entry_price {entry_price} < min {min_entry}"

    return True, "ok"


# ---------------------------------------------------------------------------
# Scoring (mirrors paper_trading._score_and_sort_opportunities)
# ---------------------------------------------------------------------------

def _score_and_sort(opps, strategy):
    """Score and sort opportunities by composite score (best first)."""
    alloc = strategy.get("capital_allocation", {})
    sort_field = alloc.get("sort_field", "composite")
    weights = alloc.get("sort_weights", {"edge": 0.4, "confidence": 0.3, "ev_per_dollar": 0.3})

    for opp in opps:
        if sort_field == "composite":
            opp["_score"] = (
                (opp.get("edge", 0) or 0) * weights.get("edge", 0.33)
                + (opp.get("confidence", 0) or 0) * weights.get("confidence", 0.33)
                + (opp.get("ev_per_dollar", 0) or 0) * weights.get("ev_per_dollar", 0.33)
            )
        else:
            opp["_score"] = opp.get(sort_field, 0) or 0

    opps.sort(key=lambda o: o.get("_score", 0), reverse=True)
    return opps


# ---------------------------------------------------------------------------
# Position sizing from liquidity book levels (mirrors paper_trading)
# ---------------------------------------------------------------------------

def _compute_position(liquidity):
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
# CLOB client management
# ---------------------------------------------------------------------------

def _get_default_client():
    """Build a CLOB client from env vars (default wallet)."""
    if not POLYMARKET_PRIVATE_KEY:
        raise RuntimeError("POLYMARKET_PRIVATE_KEY not set")

    if CLOB_API_KEY and CLOB_API_SECRET and CLOB_API_PASSPHRASE:
        creds = ApiCreds(
            api_key=CLOB_API_KEY,
            api_secret=CLOB_API_SECRET,
            api_passphrase=CLOB_API_PASSPHRASE,
        )
        client = ClobClient(CLOB_HOST, key=POLYMARKET_PRIVATE_KEY, chain_id=POLYGON, creds=creds)
    else:
        client = ClobClient(CLOB_HOST, key=POLYMARKET_PRIVATE_KEY, chain_id=POLYGON, signature_type=0)
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        _log(f"Derived CLOB creds (save for faster startup): key={creds.api_key[:8]}...")

    return client


def _get_client_for_portfolio(portfolio):
    """Get CLOB client for a portfolio, using WalletManager if available."""
    # Try WalletManager first (being built by another agent)
    try:
        from wallet_manager import WalletManager
        wm = WalletManager()
        return wm.get_client(portfolio)
    except (ImportError, Exception):
        pass

    # Fall back to default client
    return _get_default_client()


# ---------------------------------------------------------------------------
# Supabase data fetchers
# ---------------------------------------------------------------------------

def _fetch_live_portfolios():
    """Fetch active live portfolios from Supabase."""
    url = (
        f"{SUPABASE_URL}/rest/v1/portfolios"
        f"?active=eq.true&trade_mode=eq.live&select=*"
    )
    return _http_get(url, _supabase_headers())


def _fetch_opportunities():
    """Fetch recent scanner opportunities from Supabase.

    Returns only opps with date >= today.
    """
    url = (
        f"{SUPABASE_URL}/rest/v1/opportunities"
        f"?select=*&order=created_at.desc&limit=500"
    )
    opps = _http_get(url, _supabase_headers())

    today_str = date.today().isoformat()
    return [o for o in opps if (o.get("date") or "") >= today_str]


def _fetch_midpoint(token_id):
    """Fetch current midpoint price for a token from Polymarket CLOB."""
    url = f"{CLOB_HOST}/midpoint?token_id={urllib.parse.quote(token_id)}"
    result = _http_get(url, timeout=10)
    # Response is {"mid": "0.05"} or similar
    mid = result.get("mid")
    if mid is not None:
        return float(mid)
    return None


def _fetch_midpoints_batch(token_ids):
    """Fetch midpoints for a list of token_ids. Returns {token_id: float}."""
    midpoints = {}
    errors = 0
    for tid in token_ids:
        try:
            mid = _fetch_midpoint(tid)
            if mid is not None:
                midpoints[tid] = mid
        except Exception as e:
            errors += 1
            if errors <= 2:  # Log first 2 errors only
                _log(f"Midpoint fetch failed for {str(tid)[:20]}...: {e}")
    if errors > 0:
        _log(f"Midpoint batch: {len(midpoints)} ok, {errors} failed out of {len(token_ids)}")
    return midpoints


def _check_duplicate(opp, portfolio_id):
    """Check if a trade already exists for this opp+portfolio."""
    city = urllib.parse.quote(opp.get("city", ""), safe="")
    dt = urllib.parse.quote(opp.get("date", ""), safe="")
    band_c = urllib.parse.quote(str(opp.get("band_c", "")), safe="")
    side = urllib.parse.quote(opp.get("side", ""), safe="")

    url = (
        f"{SUPABASE_URL}/rest/v1/paper_trades"
        f"?city=eq.{city}&date=eq.{dt}&band_c=eq.{band_c}"
        f"&side=eq.{side}&portfolio_id=eq.{portfolio_id}"
        f"&status=in.(open,pending_execution)"
        f"&select=id"
    )
    try:
        existing = _http_get(url, _supabase_headers())
        return len(existing) > 0
    except Exception:
        return False  # If check fails, allow trade (fail open)


def _fetch_deployed_capital(portfolio_id):
    """Fetch open trades to calculate deployed capital and city exposure."""
    url = (
        f"{SUPABASE_URL}/rest/v1/paper_trades"
        f"?status=eq.open&portfolio_id=eq.{portfolio_id}&select=total_cost_usd,city"
    )
    open_trades = _http_get(url, _supabase_headers())

    deployed = sum(float(t.get("total_cost_usd", 0) or 0) for t in open_trades)

    city_exposure = {}
    for t in open_trades:
        c = t.get("city", "")
        city_exposure[c] = city_exposure.get(c, 0.0) + float(t.get("total_cost_usd", 0) or 0)

    return deployed, city_exposure, open_trades


def _fetch_realized_pnl(portfolio_id):
    """Fetch realized P&L from resolved trades."""
    url = (
        f"{SUPABASE_URL}/rest/v1/paper_trades"
        f"?status=in.(won,lost)&portfolio_id=eq.{portfolio_id}&select=profit_usd"
    )
    resolved = _http_get(url, _supabase_headers())
    return sum(float(t.get("profit_usd", 0) or 0) for t in resolved)


# ---------------------------------------------------------------------------
# Execution log
# ---------------------------------------------------------------------------

def _log_execution(trade_id=None, portfolio_id=None, action="",
                   request_payload=None, response_payload=None,
                   error_message=None, duration_ms=None):
    """Write an entry to execution_log table."""
    try:
        row = {
            "action": action,
            "request_payload": request_payload,
            "response_payload": response_payload,
            "error_message": error_message,
            "duration_ms": duration_ms,
        }
        if trade_id:
            row["trade_id"] = trade_id
        if portfolio_id:
            row["portfolio_id"] = portfolio_id

        url = f"{SUPABASE_URL}/rest/v1/execution_log"
        _http_post(url, [row], _supabase_headers())
    except Exception as e:
        _log(f"Failed to write execution log: {e}")


# ---------------------------------------------------------------------------
# Core trade execution
# ---------------------------------------------------------------------------

def _execute_trade(client, opp, position, portfolio_id):
    """Sign and post an order to the CLOB. Returns (success, result_dict)."""
    token_id = opp.get("token_id", "")
    if not token_id:
        return False, {"error": "no token_id"}

    price = position["entry_price"]
    size = position["total_shares"]

    # Clamp price to valid range
    if price <= 0 or price >= 1:
        return False, {"error": f"invalid price {price}"}
    if size <= 0:
        return False, {"error": f"invalid size {size}"}

    order_args = OrderArgs(
        token_id=token_id,
        price=price,
        size=round(size, 2),
        side=BUY,
    )

    # Snapshot balance before
    usdc_before = None
    try:
        bal = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        usdc_before = float(bal.get("balance", "0")) / 1e6
    except Exception:
        pass

    signed_order = client.create_order(order_args)
    result = client.post_order(signed_order, orderType=OrderType.GTC)

    order_id = result.get("orderID", result.get("order_id", ""))
    status = result.get("status", "")

    # Wait briefly for fill, then get actual data
    import time as _time
    _time.sleep(2)

    # Get fill details
    fill_data = None
    try:
        if order_id:
            order_info = client.get_order(order_id)
            fill_data = {
                "status": order_info.get("status", status),
                "size_matched": order_info.get("size_matched", "0"),
                "price": order_info.get("price"),
                "associate_trades": order_info.get("associate_trades", []),
            }
    except Exception:
        pass

    # Snapshot balance after
    usdc_after = None
    try:
        bal = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        usdc_after = float(bal.get("balance", "0")) / 1e6
    except Exception:
        pass

    # Actual cost from balance change
    actual_cost = round(usdc_before - usdc_after, 6) if usdc_before is not None and usdc_after is not None else None
    estimated_cost = round(price * size, 2)

    return True, {
        "order_id": order_id,
        "status": status,
        "estimated_cost_usd": estimated_cost,
        "actual_cost_usd": actual_cost,
        "usdc_before": usdc_before,
        "usdc_after": usdc_after,
        "fill_data": fill_data,
    }


# ---------------------------------------------------------------------------
# TradingLoop class
# ---------------------------------------------------------------------------

class TradingLoop:
    """Background trading loop that runs alongside Flask."""

    def __init__(self, app=None):
        self._app = app
        self._thread = None
        self._running = False
        self._paused = False
        self._stop_event = threading.Event()

        # State
        self._portfolios = []
        self._opportunities = []
        self._midpoints = {}  # token_id -> float
        self._last_portfolio_fetch = 0
        self._last_opp_fetch = 0
        self._last_price_poll = 0
        self._cycle_count = 0
        self._trades_placed = 0
        self._last_cycle_time = None
        self._last_error = None
        self._errors_total = 0

    def start(self):
        """Start the trading loop in a daemon thread."""
        if self._running:
            _log("Already running")
            return

        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            _log("Cannot start: SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
            return

        self._stop_event.clear()
        self._running = True
        self._paused = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="trading-loop")
        self._thread.start()
        _log("Started")

    def stop(self):
        """Stop the trading loop."""
        self._running = False
        self._stop_event.set()
        _log("Stopped")

    def pause(self):
        """Toggle pause state."""
        self._paused = not self._paused
        _log(f"{'Paused' if self._paused else 'Resumed'}")

    def status(self):
        """Return current loop status dict."""
        return {
            "running": self._running,
            "paused": self._paused,
            "cycle_count": self._cycle_count,
            "trades_placed": self._trades_placed,
            "last_cycle_time": self._last_cycle_time,
            "portfolios_cached": len(self._portfolios),
            "opportunities_cached": len(self._opportunities),
            "midpoints_cached": len(self._midpoints),
            "errors_total": self._errors_total,
            "last_error": self._last_error,
        }

    # -------------------------------------------------------------------
    # Main loop
    # -------------------------------------------------------------------

    def _run(self):
        """Main loop entry point. Never crashes — logs errors and continues."""
        _log("Loop thread starting")
        _log_execution(action="loop_started")

        while self._running and not self._stop_event.is_set():
            try:
                if self._paused:
                    self._stop_event.wait(timeout=1)
                    continue

                now = time.time()

                # Refresh portfolios
                if now - self._last_portfolio_fetch >= PORTFOLIO_REFRESH_INTERVAL:
                    self._refresh_portfolios()
                    self._last_portfolio_fetch = now

                # Refresh opportunities
                if now - self._last_opp_fetch >= OPP_REFRESH_INTERVAL:
                    self._refresh_opportunities()
                    self._last_opp_fetch = now

                # Poll prices and check for trades
                if now - self._last_price_poll >= PRICE_POLL_INTERVAL:
                    if self._opportunities and self._portfolios:
                        self._poll_and_trade()
                    self._last_price_poll = now

                self._cycle_count += 1
                self._last_cycle_time = datetime.now(timezone.utc).isoformat()

                # Sleep briefly to avoid busy-waiting
                self._stop_event.wait(timeout=0.5)

            except Exception as e:
                self._errors_total += 1
                self._last_error = f"{datetime.now(timezone.utc).isoformat()}: {str(e)}"
                _log(f"Loop error (continuing): {e}")
                traceback.print_exc()
                # Back off on errors
                self._stop_event.wait(timeout=5)

        _log("Loop thread exiting")
        _log_execution(action="loop_stopped")
        self._running = False

    # -------------------------------------------------------------------
    # Data refresh
    # -------------------------------------------------------------------

    def _refresh_portfolios(self):
        """Fetch active live portfolios from Supabase."""
        try:
            portfolios = _fetch_live_portfolios()
            self._portfolios = portfolios
            _log(f"Refreshed portfolios: {len(portfolios)} active live")
        except Exception as e:
            self._errors_total += 1
            self._last_error = f"portfolio_fetch: {e}"
            _log(f"Failed to fetch portfolios: {e}")

    def _refresh_opportunities(self):
        """Fetch and cache scanner opportunities from Supabase."""
        try:
            opps = _fetch_opportunities()
            self._opportunities = opps

            # Collect unique token_ids
            token_ids = set()
            for o in opps:
                tid = o.get("token_id")
                if tid:
                    token_ids.add(tid)

            _log(f"Refreshed opportunities: {len(opps)} (tokens: {len(token_ids)})")
        except Exception as e:
            self._errors_total += 1
            self._last_error = f"opp_fetch: {e}"
            _log(f"Failed to fetch opportunities: {e}")

    # -------------------------------------------------------------------
    # Price polling and trade evaluation
    # -------------------------------------------------------------------

    def _poll_and_trade(self):
        """Poll midpoints for all tokens, then evaluate edges per portfolio."""
        # Collect all unique token_ids
        token_ids = set()
        for o in self._opportunities:
            tid = o.get("token_id")
            if tid:
                token_ids.add(tid)

        if not token_ids:
            return

        # Batch fetch midpoints
        try:
            self._midpoints = _fetch_midpoints_batch(list(token_ids))
            if self._cycle_count % 100 == 0:  # Log every 100 cycles
                _log(f"Midpoints: {len(self._midpoints)}/{len(token_ids)} tokens priced")
        except Exception as e:
            _log(f"Midpoint fetch error: {e}")
            return

        # Evaluate each portfolio
        for portfolio in self._portfolios:
            try:
                self._evaluate_portfolio(portfolio)
            except Exception as e:
                pf_name = portfolio.get("name", portfolio.get("id", "?"))
                self._errors_total += 1
                self._last_error = f"portfolio_{pf_name}: {e}"
                _log(f"Error evaluating portfolio {pf_name}: {e}")

    def _evaluate_portfolio(self, portfolio):
        """Evaluate all opportunities against a single portfolio."""
        portfolio_id = portfolio.get("id")
        pf_name = portfolio.get("name", str(portfolio_id))
        strategy = portfolio.get("strategy", {})

        # Check trading hours
        hours_ok, hours_reason = _check_trading_hours(strategy)
        if not hours_ok:
            if self._cycle_count % 200 == 0:
                _log(f"[{pf_name}] Hours blocked: {hours_reason}")
            return  # Silent skip — logged only on state change

        # Capital management setup
        use_capital_mgmt = (
            not portfolio.get("unlimited_capital", True)
            and portfolio.get("starting_capital_usd") is not None
        )

        deployed = 0.0
        city_exposure = {}
        current_capital = 0.0
        max_single_trade_usd = 999999
        max_single_trade_pct = 100
        max_portfolio_util_pct = 100
        max_corr_exposure_pct = 100

        if use_capital_mgmt:
            try:
                starting_capital = float(portfolio.get("starting_capital_usd", 0))
                cap_mgmt = strategy.get("capital_management", {})
                max_single_trade_usd = float(cap_mgmt.get("max_single_trade_usd", 999999))
                max_single_trade_pct = float(cap_mgmt.get("max_single_trade_pct", 100))
                max_portfolio_util_pct = float(cap_mgmt.get("max_portfolio_utilization_pct", 100))
                max_corr_exposure_pct = float(cap_mgmt.get("max_correlated_exposure_pct", 100))

                deployed, city_exposure, _ = _fetch_deployed_capital(portfolio_id)
                realized_pnl = _fetch_realized_pnl(portfolio_id)
                current_capital = starting_capital + realized_pnl
            except Exception as e:
                _log(f"Capital fetch error for {pf_name}: {e}")
                return  # Skip this portfolio this cycle

        # Score and sort opportunities
        opps = list(self._opportunities)  # shallow copy
        opps = _score_and_sort(opps, strategy)

        # Debug: log evaluation summary every 200 cycles
        if self._cycle_count % 200 == 0:
            dbg = {"filter":0, "no_token":0, "no_midpoint":0, "no_edge":0, "no_liq":0, "no_pos":0, "cost_low":0, "shares_low":0, "cap":0, "dup":0, "ready":0}
            for o in opps:
                p, _ = _passes_strategy_filters(o, strategy)
                if not p: dbg["filter"]+=1; continue
                tid = o.get("token_id", "")
                if not tid: dbg["no_token"]+=1; continue
                if tid not in self._midpoints: dbg["no_midpoint"]+=1; continue
                mp = self._midpoints[tid]
                e = (o.get("my_p") or 0) - (mp * 100)
                if e < MIN_EDGE_PP: dbg["no_edge"]+=1; continue
                liq = o.get("liquidity")
                if not liq: dbg["no_liq"]+=1; continue
                pos = _compute_position(liq)
                if pos is None: dbg["no_pos"]+=1; continue
                if pos["total_cost_usd"] < 5.0: dbg["cost_low"]+=1; continue
                if pos["total_shares"] < 5.0: dbg["shares_low"]+=1; continue
                dbg["ready"]+=1
            _log(f"[{pf_name}] Eval: {len(opps)} opps → {dbg}")

        # Get CLOB client (lazy, once per cycle per portfolio)
        client = None

        for opp in opps:
            opp_label = f"{opp.get('city', '?')}/{opp.get('band_c', '?')}/{opp.get('side', '?')}"
            try:
                # Strategy filters
                passes, reason = _passes_strategy_filters(opp, strategy)
                if not passes:
                    continue

                # Check we have a midpoint for this token
                token_id = opp.get("token_id", "")
                if not token_id or token_id not in self._midpoints:
                    continue

                mkt_price = self._midpoints[token_id]
                my_p = opp.get("my_p")
                if my_p is None:
                    continue

                # Calculate real-time edge
                # token_id is side-specific: YES opps have YES token,
                # NO opps have NO token. Midpoint is already the correct price.
                opp_side = opp.get("side", "")
                realtime_edge = my_p - (mkt_price * 100)

                # Must exceed minimum edge
                if realtime_edge < MIN_EDGE_PP:
                    continue

                # Check liquidity / position sizing
                liquidity = opp.get("liquidity")
                if not liquidity:
                    continue
                position = _compute_position(liquidity)
                if position is None:
                    continue
                if position["total_cost_usd"] < 5.0:
                    continue

                cost = position["total_cost_usd"]
                fees = cost * POLYMARKET_FEE_RATE
                total_with_fees = cost + fees

                # Capital management checks
                if use_capital_mgmt:
                    max_by_pct = current_capital * max_single_trade_pct / 100
                    capped_cost = min(cost, max_single_trade_usd, max_by_pct)
                    if capped_cost < cost:
                        scale = capped_cost / cost if cost > 0 else 1
                        position["total_cost_usd"] = round(capped_cost, 2)
                        position["total_shares"] = round(position["total_shares"] * scale, 2)
                        cost = capped_cost
                        fees = cost * POLYMARKET_FEE_RATE
                        total_with_fees = cost + fees

                    max_deployed = current_capital * max_portfolio_util_pct / 100
                    if deployed + total_with_fees > max_deployed:
                        continue

                    opp_city = opp.get("city", "")
                    city_exp = city_exposure.get(opp_city, 0.0)
                    max_city = current_capital * max_corr_exposure_pct / 100
                    if city_exp + total_with_fees > max_city:
                        continue

                # Polymarket minimums: 5 shares AND $1 marketable order
                if position["total_shares"] < 5.0:
                    continue
                if position["total_cost_usd"] < 1.10:
                    continue

                # Deduplication check
                if _check_duplicate(opp, portfolio_id):
                    continue

                # --- Execute trade ---
                _log(f"EDGE DETECTED: {opp_label} edge={realtime_edge:.1f}pp "
                     f"mkt={mkt_price:.4f} my_p={my_p:.1f} cost=${cost:.2f} [{pf_name}]")

                # Insert trade record as pending
                trade_row = {
                    "city": opp.get("city", ""),
                    "date": opp.get("date", ""),
                    "band_c": opp.get("band_c", ""),
                    "band_f": opp.get("band_f", ""),
                    "band_type": opp.get("band_type", ""),
                    "side": opp_side,
                    "bet_type": opp.get("bet_type", ""),
                    "entry_price": position["entry_price"],
                    "total_cost_usd": position["total_cost_usd"],
                    "total_shares": position["total_shares"],
                    "num_levels": position.get("num_levels"),
                    "my_p": my_p,
                    "mkt_p": mkt_price * 100,
                    "edge": round(realtime_edge, 2),
                    "confidence": opp.get("confidence"),
                    "ev_per_dollar": opp.get("ev_per_dollar"),
                    "half_kelly": opp.get("half_kelly") or opp.get("hk"),
                    "forecast_c": opp.get("forecast_c"),
                    "risk": opp.get("risk"),
                    "question": opp.get("question"),
                    "empirical_p": opp.get("empirical_p"),
                    "price_source": opp.get("price_source"),
                    "token_id": token_id,
                    "condition_id": opp.get("condition_id"),
                    "event_slug": opp.get("event_slug"),
                    "market_slug": opp.get("market_slug"),
                    "url": opp.get("url"),
                    "liquidity": liquidity,
                    "status": "pending_execution",
                    "trade_mode": "live",
                    "portfolio_id": portfolio_id,
                }

                try:
                    insert_url = f"{SUPABASE_URL}/rest/v1/paper_trades"
                    result = _http_post(insert_url, [trade_row], _supabase_headers())
                    trade_id = result[0].get("id") if isinstance(result, list) and result else None
                except urllib.error.HTTPError as e:
                    if e.code == 409:
                        _log(f"Duplicate trade: {opp_label}")
                        continue
                    raise

                if not trade_id:
                    _log(f"Failed to insert trade record for {opp_label}")
                    continue

                _log_execution(
                    trade_id=trade_id, portfolio_id=portfolio_id,
                    action="trade_inserted",
                    request_payload={
                        "opp": opp_label, "cost": cost,
                        "shares": position["total_shares"],
                        "price": position["entry_price"],
                        "edge": round(realtime_edge, 2),
                        "source": "trading_loop",
                    },
                )

                # Get CLOB client lazily
                if client is None:
                    client = _get_client_for_portfolio(portfolio)

                t_exec = time.time()
                try:
                    success, exec_result = _execute_trade(client, opp, position, portfolio_id)
                    exec_ms = int((time.time() - t_exec) * 1000)

                    if success:
                        actual_cost = exec_result.get("actual_cost_usd")
                        estimated_cost = exec_result.get("estimated_cost_usd", cost)
                        final_cost = actual_cost if actual_cost is not None else estimated_cost
                        fill_data = exec_result.get("fill_data", {})
                        size_matched = fill_data.get("size_matched", "0") if fill_data else "0"

                        _log(f"EXECUTED: {opp_label} actual=${actual_cost} est=${estimated_cost} "
                             f"matched={size_matched} ({exec_ms}ms) [{pf_name}]")
                        self._trades_placed += 1

                        # Update with actual on-chain data
                        trade_update = {
                            "status": "open",
                            "execution_details": {
                                "order_id": exec_result.get("order_id"),
                                "order_status": exec_result.get("status"),
                                "estimated_cost_usd": estimated_cost,
                                "actual_cost_usd": actual_cost,
                                "usdc_before": exec_result.get("usdc_before"),
                                "usdc_after": exec_result.get("usdc_after"),
                                "fill_data": fill_data,
                                "executed_at": datetime.now(timezone.utc).isoformat(),
                                "source": "trading_loop",
                            },
                        }
                        # Override with actuals if available
                        if actual_cost is not None and actual_cost > 0:
                            trade_update["total_cost_usd"] = round(actual_cost, 4)
                        if fill_data and fill_data.get("size_matched") and float(fill_data["size_matched"]) > 0:
                            trade_update["total_shares"] = round(float(fill_data["size_matched"]), 4)
                            if actual_cost and float(fill_data["size_matched"]) > 0:
                                trade_update["entry_price"] = round(actual_cost / float(fill_data["size_matched"]), 6)

                        open_url = f"{SUPABASE_URL}/rest/v1/paper_trades?id=eq.{trade_id}"
                        _http_patch(open_url, trade_update, _supabase_headers())

                        _log_execution(
                            trade_id=trade_id, portfolio_id=portfolio_id,
                            action="trade_executed",
                            response_payload=exec_result,
                            duration_ms=exec_ms,
                        )

                        # Update deployed tracking with actual cost
                        if use_capital_mgmt:
                            deployed += final_cost
                            opp_city = opp.get("city", "")
                            city_exposure[opp_city] = city_exposure.get(opp_city, 0.0) + final_cost
                    else:
                        error = exec_result.get("error", "unknown")
                        _log(f"Execution failed: {opp_label}: {error}")

                        fail_url = f"{SUPABASE_URL}/rest/v1/paper_trades?id=eq.{trade_id}"
                        _http_patch(fail_url, {
                            "status": "void",
                            "execution_details": {"error": error},
                        }, _supabase_headers())

                        _log_execution(
                            trade_id=trade_id, portfolio_id=portfolio_id,
                            action="trade_failed",
                            response_payload=exec_result,
                            error_message=error, duration_ms=exec_ms,
                        )

                except Exception as exec_err:
                    exec_ms = int((time.time() - t_exec) * 1000)
                    _log(f"CLOB execution error: {opp_label}: {exec_err}")

                    fail_url = f"{SUPABASE_URL}/rest/v1/paper_trades?id=eq.{trade_id}"
                    try:
                        _http_patch(fail_url, {
                            "status": "void",
                            "execution_details": {"error": str(exec_err)},
                        }, _supabase_headers())
                    except Exception:
                        pass

                    _log_execution(
                        trade_id=trade_id, portfolio_id=portfolio_id,
                        action="execution_error",
                        error_message=str(exec_err), duration_ms=exec_ms,
                    )

            except Exception as e:
                self._errors_total += 1
                _log(f"Error processing {opp_label}: {e}")
                continue
