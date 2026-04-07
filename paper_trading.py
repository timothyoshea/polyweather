"""
Paper Trading System for PolyWeather.

Tracks hypothetical trades based on scanner opportunities,
resolves them against actual temperatures, and computes P&L.
"""
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, date
import re


def compute_position_from_book_levels(liquidity):
    """Compute a VWAP position from positive-edge book levels.

    Args:
        liquidity: dict with 'book_levels' key containing list of level dicts.

    Returns:
        dict with entry_price, total_cost_usd, total_shares, num_levels
        or None if no positive-edge levels.
    """
    if not liquidity or not isinstance(liquidity, dict):
        return None

    book_levels = liquidity.get("book_levels", [])
    if not book_levels:
        return None

    # Filter to levels with positive edge
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


def _supabase_request(url, data, headers, method="POST", timeout=10):
    """Make a Supabase REST API request."""
    encoded = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=encoded, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body.strip() else []


def _supabase_get(url, headers, timeout=10):
    """Make a Supabase REST API GET request."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _score_and_sort_opportunities(opps, strategy):
    """Score and sort opportunities based on capital_allocation strategy.

    Args:
        opps: list of opportunity dicts
        strategy: portfolio strategy dict

    Returns:
        sorted list (best first), mutated with '_score' key
    """
    alloc = strategy.get('capital_allocation', {})
    sort_field = alloc.get('sort_field', 'composite')
    weights = alloc.get('sort_weights', {'edge': 0.4, 'confidence': 0.3, 'ev_per_dollar': 0.3})

    for opp in opps:
        if sort_field == 'composite':
            opp['_score'] = (
                (opp.get('edge', 0) or 0) * weights.get('edge', 0.33) +
                (opp.get('confidence', 0) or 0) * weights.get('confidence', 0.33) +
                (opp.get('ev_per_dollar', 0) or 0) * weights.get('ev_per_dollar', 0.33)
            )
        else:
            opp['_score'] = opp.get(sort_field, 0) or 0

    opps.sort(key=lambda o: o.get('_score', 0), reverse=True)
    return opps


def _check_trading_hours(strategy):
    """Check if current UTC time is within allowed trading hours.

    Strategy can include:
        "trading_hours": {
            "enabled": true,
            "allowed_windows": [{"start": "06:00", "end": "22:00"}],
            "blackout_windows": [{"start": "14:00", "end": "14:30"}]
        }

    Returns:
        (allowed: bool, reason: str)
    """
    trading_hours = strategy.get("trading_hours")
    if not trading_hours or not trading_hours.get("enabled", False):
        return True, "no restrictions"

    from datetime import timezone
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

    blackout_windows = trading_hours.get("blackout_windows", [])
    for bw in blackout_windows:
        if _in_window(bw.get("start", "00:00"), bw.get("end", "00:00")):
            return False, f"blackout {bw['start']}-{bw['end']} (now={current_time_str} UTC)"

    allowed_windows = trading_hours.get("allowed_windows", [])
    if allowed_windows:
        for aw in allowed_windows:
            if _in_window(aw.get("start", "00:00"), aw.get("end", "23:59")):
                return True, f"allowed {aw['start']}-{aw['end']}"
        return False, f"outside allowed windows (now={current_time_str} UTC)"

    return True, "no restrictions"


def open_paper_trades(opps, scan_id, supabase_url, supabase_service_key,
                      portfolio_id=None, portfolio=None):
    """Open paper trades for scanner opportunities with liquidity data.

    For each opportunity, computes a position from book levels and upserts
    into paper_trades. Also inserts a trade_snapshot for tracking over time.

    Args:
        opps: list of opportunity dicts from the scanner
        scan_id: UUID of the current scan
        supabase_url: Supabase project URL
        supabase_service_key: Supabase service role key
        portfolio_id: optional portfolio UUID to tag trades with
        portfolio: optional portfolio dict with strategy, starting_capital_usd, unlimited_capital
    """
    headers = {
        "apikey": supabase_service_key,
        "Authorization": f"Bearer {supabase_service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    # --- Check trading hours ---
    strategy = (portfolio or {}).get("strategy", {})
    hours_ok, hours_reason = _check_trading_hours(strategy)
    if not hours_ok:
        pf_name = (portfolio or {}).get("name", str(portfolio_id))
        print(f"[PAPER] Skipping {pf_name} — {hours_reason}")
        return

    # --- Capital management setup ---
    use_capital_mgmt = (
        portfolio is not None
        and not portfolio.get("unlimited_capital", True)
        and portfolio.get("starting_capital_usd") is not None
    )

    if use_capital_mgmt:
        starting_capital = float(portfolio.get("starting_capital_usd", 0))
        strategy = portfolio.get("strategy", {})
        cap_mgmt = strategy.get("capital_management", {})
        max_single_trade_usd = float(cap_mgmt.get("max_single_trade_usd", 999999))
        max_single_trade_pct = float(cap_mgmt.get("max_single_trade_pct", 100))
        max_portfolio_util_pct = float(cap_mgmt.get("max_portfolio_utilization_pct", 100))
        max_corr_exposure_pct = float(cap_mgmt.get("max_correlated_exposure_pct", 100))

        # Fetch current deployed capital
        deployed_url = (
            f"{supabase_url}/rest/v1/paper_trades"
            f"?status=eq.open&portfolio_id=eq.{portfolio_id}&select=total_cost_usd,city"
        )
        open_trades = _supabase_get(deployed_url, headers)
        deployed = sum(float(t.get("total_cost_usd", 0) or 0) for t in open_trades)

        # Build city exposure map from open trades
        city_exposure = {}
        for t in open_trades:
            c = t.get("city", "")
            city_exposure[c] = city_exposure.get(c, 0.0) + float(t.get("total_cost_usd", 0) or 0)

        # Fetch realized P&L
        pnl_url = (
            f"{supabase_url}/rest/v1/paper_trades"
            f"?status=in.(won,lost)&portfolio_id=eq.{portfolio_id}&select=profit_usd"
        )
        resolved_trades = _supabase_get(pnl_url, headers)
        realized_pnl = sum(float(t.get("profit_usd", 0) or 0) for t in resolved_trades)

        current_capital = starting_capital + realized_pnl
        available = current_capital - deployed

        print(f"[CAPITAL] starting=${starting_capital:.2f} realized_pnl=${realized_pnl:.2f} "
              f"current=${current_capital:.2f} deployed=${deployed:.2f} available=${available:.2f}")

        # Score and sort opportunities
        opps = _score_and_sort_opportunities(opps, strategy)

    # --- Strategy filters (per-portfolio) ---
    strategy = (portfolio or {}).get("strategy", {})
    allowed_sides = strategy.get("allowed_sides")       # e.g. ["NO"]
    allowed_bet_types = strategy.get("allowed_bet_types")  # e.g. ["safe_no", "edge"]
    allowed_band_types = strategy.get("allowed_band_types")  # e.g. ["above", "below"]
    blocked_cities = strategy.get("blocked_cities", [])
    allowed_cities = strategy.get("allowed_cities", [])  # empty = all allowed

    count = 0
    for opp in opps:
        try:
            # --- Strategy filters ---
            opp_side = opp.get("side", "")
            opp_bet_type = opp.get("bet_type", "")
            opp_band_type = opp.get("band_type", "")
            opp_city = opp.get("city", "")

            if allowed_sides and opp_side not in allowed_sides:
                continue
            if allowed_bet_types and opp_bet_type not in allowed_bet_types:
                continue
            if allowed_band_types and opp_band_type not in allowed_band_types:
                continue
            if blocked_cities and opp_city in blocked_cities:
                continue
            if allowed_cities and opp_city not in allowed_cities:
                continue

            # Entry price filter
            min_entry = strategy.get("preferred_entry_price_min")
            if min_entry is not None:
                opp_entry = opp.get("entry_price") or (opp.get("mkt_p", 0) / 100 if opp.get("mkt_p") else None)
                if opp_entry and float(opp_entry) < float(min_entry):
                    continue

            # Max confidence filter (per bet type)
            opp_confidence = opp.get("confidence", 0) or 0
            if opp_bet_type == "edge":
                max_conf = strategy.get("edge_bet", {}).get("max_confidence")
                if max_conf is not None and opp_confidence > float(max_conf):
                    continue
            elif opp_bet_type == "safe_no":
                max_conf = strategy.get("safe_no", {}).get("max_confidence")
                if max_conf is not None and opp_confidence > float(max_conf):
                    continue

            # Max edge filter (edge bets only)
            if opp_bet_type == "edge":
                max_edge = strategy.get("edge_bet", {}).get("max_edge")
                opp_edge = opp.get("edge", 0) or 0
                if max_edge is not None and opp_edge > float(max_edge) * 100:
                    continue

            # Ensemble std filter (min and max)
            fd = opp.get("forecast_details") or {}
            ens_std = fd.get("ensemble_std")
            ens_std_min = strategy.get("ensemble_std_min")
            if ens_std_min is not None and ens_std is not None:
                if float(ens_std) < float(ens_std_min):
                    continue
            ens_std_max = strategy.get("ensemble_std_max")
            if ens_std_max is not None and ens_std is not None:
                if float(ens_std) > float(ens_std_max):
                    continue

            liquidity = opp.get("liquidity")
            if not liquidity:
                continue

            position = compute_position_from_book_levels(liquidity)
            if position is None:
                continue
            if position["total_cost_usd"] < 5.0:
                continue

            # --- Capital checks ---
            cost = position["total_cost_usd"]
            if use_capital_mgmt:
                # Cap position size
                max_by_pct = current_capital * max_single_trade_pct / 100
                capped_cost = min(cost, max_single_trade_usd, max_by_pct)
                if capped_cost < cost:
                    # Scale shares proportionally
                    scale = capped_cost / cost if cost > 0 else 1
                    position["total_cost_usd"] = round(capped_cost, 2)
                    position["total_shares"] = round(position["total_shares"] * scale, 2)
                    cost = capped_cost

                # Check portfolio utilization
                max_deployed = current_capital * max_portfolio_util_pct / 100
                if deployed + cost > max_deployed:
                    print(f"[CAPITAL] SKIP {opp.get('city')}/{opp.get('band_c')}: "
                          f"deployed ${deployed:.2f} + ${cost:.2f} > max ${max_deployed:.2f} "
                          f"({max_portfolio_util_pct}% utilization)")
                    continue

                # Check correlated (city) exposure
                city_name = opp.get("city", "")
                city_exp = city_exposure.get(city_name, 0.0)
                max_city = current_capital * max_corr_exposure_pct / 100
                if city_exp + cost > max_city:
                    print(f"[CAPITAL] SKIP {city_name}/{opp.get('band_c')}: "
                          f"city exposure ${city_exp:.2f} + ${cost:.2f} > max ${max_city:.2f} "
                          f"({max_corr_exposure_pct}% correlated)")
                    continue

                # Update running totals for subsequent iterations
                deployed += cost
                city_exposure[city_name] = city_exp + cost

            trade_row = {
                "city": opp.get("city", ""),
                "date": opp.get("date", ""),
                "band_c": opp.get("band_c", ""),
                "band_f": opp.get("band_f", ""),
                "band_type": opp.get("band_type", ""),
                "side": opp.get("side", ""),
                "bet_type": opp.get("bet_type", ""),
                "entry_price": position["entry_price"],
                "total_cost_usd": position["total_cost_usd"],
                "total_shares": position["total_shares"],
                "num_levels": position["num_levels"],
                "my_p": opp.get("my_p"),
                "mkt_p": opp.get("mkt_p"),
                "edge": opp.get("edge"),
                "confidence": opp.get("confidence"),
                "ev_per_dollar": opp.get("ev_per_dollar"),
                "half_kelly": opp.get("hk"),
                "forecast_c": opp.get("forecast_c"),
                "risk": opp.get("risk"),
                "empirical_p": opp.get("empirical_p"),
                "price_source": opp.get("price_source"),
                "question": opp.get("question"),
                "token_id": opp.get("token_id"),
                "condition_id": opp.get("condition_id"),
                "event_slug": opp.get("event_slug"),
                "market_slug": opp.get("market_slug"),
                "url": opp.get("url"),
                "liquidity": liquidity,
                "model_values": opp.get("model_values"),
                "forecast_details": {
                    **{k: opp.get(k) for k in [
                        "combined_forecast", "ensemble_mean", "ensemble_std",
                        "ensemble_min", "ensemble_max", "multi_model_spread",
                        "eff_std", "horizon_days", "city_tier",
                    ] if opp.get(k) is not None},
                    "model_weights": opp.get("model_weights", {}),
                },
                "status": "open",
            }

            if portfolio_id:
                trade_row["portfolio_id"] = portfolio_id

            # Try INSERT; on conflict (city, date, band_c, side), accumulate new liquidity
            trade_id = None
            is_new_trade = False
            try:
                insert_url = f"{supabase_url}/rest/v1/paper_trades"
                result = _supabase_request(insert_url, [trade_row], headers)
                if result and len(result) > 0:
                    trade_id = result[0].get("id")
                    is_new_trade = True
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    # Trade exists — fetch it and check for new price levels
                    city_enc = urllib.parse.quote(opp.get("city", ""))
                    date_enc = urllib.parse.quote(opp.get("date", ""))
                    band_enc = urllib.parse.quote(opp.get("band_c", ""))
                    side_enc = urllib.parse.quote(opp.get("side", ""))
                    pf_filter = f"&portfolio_id=eq.{portfolio_id}" if portfolio_id else "&portfolio_id=is.null"
                    select_url = (
                        f"{supabase_url}/rest/v1/paper_trades"
                        f"?city=eq.{city_enc}&date=eq.{date_enc}"
                        f"&band_c=eq.{band_enc}&side=eq.{side_enc}"
                        f"{pf_filter}&select=*&status=eq.open"
                    )
                    existing = _supabase_get(select_url, headers)
                    if existing and len(existing) > 0:
                        ex = existing[0]
                        trade_id = ex.get("id")

                        # Check for genuinely NEW price levels we haven't seen before.
                        # Compare the set of price_cents in current book vs what we hold.
                        prev_liq = ex.get("liquidity") or {}
                        prev_levels = prev_liq.get("book_levels") or []
                        prev_prices = set()
                        for lv in prev_levels:
                            if lv.get("edge_pp", 0) > 0:
                                prev_prices.add(round(lv.get("price_cents", 0), 1))

                        curr_levels = (liquidity or {}).get("book_levels") or []
                        new_cost = 0.0
                        new_shares = 0.0
                        for lv in curr_levels:
                            if lv.get("edge_pp", 0) > 0:
                                price = round(lv.get("price_cents", 0), 1)
                                if price not in prev_prices:
                                    new_cost += lv.get("cost_usd", 0)
                                    new_shares += lv.get("shares", 0)

                        if new_cost > 1.0 and new_shares > 0:
                            prev_cost = float(ex.get("total_cost_usd", 0))
                            prev_shares = float(ex.get("total_shares", 0))
                            updated_cost = round(prev_cost + new_cost, 2)
                            updated_shares = round(prev_shares + new_shares, 2)
                            updated_vwap = round(updated_cost / updated_shares, 6)

                            update_url = (
                                f"{supabase_url}/rest/v1/paper_trades"
                                f"?id=eq.{trade_id}"
                            )
                            update_data = {
                                "total_cost_usd": updated_cost,
                                "total_shares": updated_shares,
                                "entry_price": updated_vwap,
                                "num_levels": position["num_levels"],
                                "liquidity": liquidity,
                                "model_values": opp.get("model_values"),
                                "forecast_details": trade_row["forecast_details"],
                            }
                            _supabase_request(update_url, update_data, headers, method="PATCH")
                            print(f"[INFO] New price level found: +${new_cost:.2f} to {opp.get('city')}/{opp.get('band_c')} (total: ${updated_cost:.2f})")

                        # Always update latest market state
                        update_url = f"{supabase_url}/rest/v1/paper_trades?id=eq.{trade_id}"
                        _supabase_request(update_url, {
                            "my_p": opp.get("my_p"),
                            "mkt_p": opp.get("mkt_p"),
                            "edge": opp.get("edge"),
                            "confidence": opp.get("confidence"),
                            "ev_per_dollar": opp.get("ev_per_dollar"),
                            "forecast_c": opp.get("forecast_c"),
                        }, headers, method="PATCH")
                else:
                    raise

            # Insert a snapshot for this scan
            if trade_id:
                liq = liquidity or {}
                snapshot_row = {
                    "trade_id": trade_id,
                    "scan_id": scan_id,
                    "my_p": opp.get("my_p"),
                    "mkt_p": opp.get("mkt_p"),
                    "edge": opp.get("edge"),
                    "confidence": opp.get("confidence"),
                    "forecast_c": opp.get("forecast_c"),
                    "ev_per_dollar": opp.get("ev_per_dollar"),
                    "total_depth_usd": liq.get("total_depth_usd"),
                    "adjusted_bet_usd": liq.get("adjusted_bet_usd"),
                    "effective_price": liq.get("effective_price"),
                    "effective_edge_pp": liq.get("effective_edge_pp"),
                    "liquidity_rating": liq.get("liquidity_rating"),
                    "book_levels": liq.get("book_levels"),
                    "positive_edge_cost_usd": position["total_cost_usd"],
                    "positive_edge_shares": position["total_shares"],
                }
                snapshot_url = f"{supabase_url}/rest/v1/trade_snapshots"
                try:
                    _supabase_request(snapshot_url, [snapshot_row], headers)
                except Exception as snap_err:
                    print(f"[WARN] Snapshot insert error for trade {trade_id}: {snap_err}")
                count += 1

        except Exception as e:
            print(f"[WARN] Paper trade error for {opp.get('city')}/{opp.get('date')}: {e}")

    return count


def fetch_actual_temperature(city, date_str, city_geo):
    """Fetch actual high temperature from Open-Meteo archive API.

    Args:
        city: city name
        date_str: date string YYYY-MM-DD
        city_geo: dict mapping city names to (lat, lng, tz, icao)

    Returns:
        float temperature in Celsius, or None if not available.
    """
    geo = city_geo.get(city)
    if not geo:
        return None

    lat, lng, tz = geo[0], geo[1], geo[2]

    url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lng}"
        f"&start_date={date_str}&end_date={date_str}"
        f"&daily=temperature_2m_max&timezone={urllib.parse.quote(tz)}"
    )

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            temps = data.get("daily", {}).get("temperature_2m_max", [])
            if temps and temps[0] is not None:
                return float(temps[0])
    except Exception as e:
        print(f"[WARN] Open-Meteo fetch error for {city}/{date_str}: {e}")

    return None


def determine_outcome(actual_temp_c, band_c, band_type, side):
    """Determine if a trade won or lost based on actual temperature.

    Args:
        actual_temp_c: actual temperature in Celsius (float)
        band_c: band string like ">=22C", "<=15C", "20-21C", "20C"
        band_type: "above", "below", or "exact"
        side: "YES" or "NO"

    Returns:
        'won' or 'lost'
    """
    # Parse the band_c string to extract thresholds
    band = band_c.strip()

    yes_wins = False

    if band_type == "below":
        # "<=15C" or similar
        m = re.search(r'(-?\d+(?:\.\d+)?)', band)
        if m:
            threshold = float(m.group(1))
            yes_wins = actual_temp_c <= threshold

    elif band_type == "above":
        # ">=22C" or similar
        m = re.search(r'(-?\d+(?:\.\d+)?)', band)
        if m:
            threshold = float(m.group(1))
            yes_wins = actual_temp_c >= threshold

    else:
        # "exact" band type: "20-21C" or "20C"
        range_match = re.match(r'(-?\d+(?:\.\d+)?)\s*[-\u2013]\s*(-?\d+(?:\.\d+)?)', band)
        if range_match:
            lo = float(range_match.group(1))
            hi = float(range_match.group(2))
            yes_wins = lo <= actual_temp_c < hi + 1
        else:
            single_match = re.search(r'(-?\d+(?:\.\d+)?)', band)
            if single_match:
                lo = float(single_match.group(1))
                yes_wins = lo <= actual_temp_c < lo + 1

    # Determine outcome based on side
    if side.upper() == "YES":
        return "won" if yes_wins else "lost"
    else:
        return "won" if not yes_wins else "lost"


def check_polymarket_resolution(market_slug):
    """Check if a Polymarket market has resolved via the Gamma API.

    Args:
        market_slug: The market slug stored in the trade (e.g. "highest-temperature-in-paris-on-april-1-2026-12c").

    Returns:
        "YES" if YES outcome won, "NO" if NO outcome won, or None if not yet resolved.
    """
    if not market_slug:
        return None

    url = f"https://gamma-api.polymarket.com/markets?slug={urllib.parse.quote(market_slug)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PolyWeather/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        markets = data if isinstance(data, list) else [data]
        if not markets:
            return None

        market = markets[0]

        # Determine winning outcome from outcomePrices
        # Markets resolve to ~1.0/0.0 prices — check that instead of
        # the unreliable "closed" flag which sometimes stays False
        prices_raw = market.get("outcomePrices", "")
        try:
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        except (json.JSONDecodeError, TypeError):
            prices = None

        if prices and len(prices) >= 2:
            yes_price = float(prices[0])
            no_price = float(prices[1])
            # Resolved: price at 0.95+ means that outcome won
            # (use 0.95 instead of 0.9 to be safe but still catch resolved markets)
            if yes_price > 0.95:
                return "YES"
            elif no_price > 0.95:
                return "NO"

    except Exception as e:
        print(f"[WARN] Polymarket resolution check error for {market_slug}: {e}")

    return None


def resolve_open_trades(supabase_url, supabase_service_key, city_geo):
    """Resolve open paper trades by checking Polymarket market resolution.

    Queries the Gamma API to see if each trade's market has resolved,
    then updates trade outcomes based on the actual Polymarket result.

    Args:
        supabase_url: Supabase project URL
        supabase_service_key: Supabase service role key
        city_geo: dict mapping city names to (lat, lng, tz, icao)

    Returns:
        dict with resolved, won, lost, skipped counts
    """
    headers = {
        "apikey": supabase_service_key,
        "Authorization": f"Bearer {supabase_service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    today_str = date.today().strftime("%Y-%m-%d")

    # Fetch all open trades (including today — Polymarket resolves same-day)
    query_url = (
        f"{supabase_url}/rest/v1/paper_trades"
        f"?status=eq.open&date=lte.{today_str}&select=*"
    )
    trades = _supabase_get(query_url, headers)

    if not trades:
        return {"resolved": 0, "won": 0, "lost": 0, "skipped": 0}

    # Cache resolution results by condition_id to avoid duplicate API calls
    resolution_cache = {}

    resolved = 0
    won = 0
    lost = 0
    skipped = 0

    for trade in trades:
        try:
            market_slug = trade.get("market_slug", "")
            side = trade.get("side", "").upper()

            if not market_slug or not side:
                skipped += 1
                continue

            # Check cache first, then Polymarket API
            if market_slug not in resolution_cache:
                resolution_cache[market_slug] = check_polymarket_resolution(market_slug)

            winning_side = resolution_cache[market_slug]
            if winning_side is None:
                skipped += 1
                continue

            # Determine outcome: did our side win?
            outcome = "won" if side == winning_side else "lost"

            total_cost = float(trade.get("total_cost_usd", 0))
            total_shares = float(trade.get("total_shares", 0))

            # Polymarket fees: charged on entry (taker) not resolution.
            # We place GTC limit orders (maker) = 0% fee.
            # No fee deduction needed at resolution.
            if outcome == "won":
                payout = total_shares * 1.0
                profit = payout - total_cost
                won += 1
            else:
                payout = 0.0
                profit = -total_cost
                lost += 1

            roi_pct = (profit / total_cost * 100) if total_cost > 0 else 0.0

            # Optionally fetch actual temp for record-keeping
            city = trade.get("city", "")
            trade_date = trade.get("date", "")
            actual_temp = fetch_actual_temperature(city, trade_date, city_geo)

            # Update trade via PATCH
            trade_id = trade.get("id")
            update_url = (
                f"{supabase_url}/rest/v1/paper_trades"
                f"?id=eq.{trade_id}"
            )
            update_data = {
                "status": outcome,
                "resolved_at": datetime.utcnow().isoformat() + "Z",
                "payout_usd": round(payout, 2),
                "profit_usd": round(profit, 2),
                "roi_pct": round(roi_pct, 2),
            }
            if actual_temp is not None:
                update_data["actual_temp_c"] = round(actual_temp, 1)

            _supabase_request(update_url, update_data, headers, method="PATCH")
            resolved += 1

            print(f"[RESOLVED] {city} {trade_date} {trade.get('band_c')} "
                  f"side={side} market={winning_side} → {outcome} "
                  f"profit=${profit:.2f}")

        except Exception as e:
            print(f"[WARN] Error resolving trade {trade.get('id')}: {e}")
            skipped += 1

    return {"resolved": resolved, "won": won, "lost": lost, "skipped": skipped}
