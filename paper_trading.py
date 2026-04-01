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


def open_paper_trades(opps, scan_id, supabase_url, supabase_service_key):
    """Open paper trades for scanner opportunities with liquidity data.

    For each opportunity, computes a position from book levels and upserts
    into paper_trades. Also inserts a trade_snapshot for tracking over time.

    Args:
        opps: list of opportunity dicts from the scanner
        scan_id: UUID of the current scan
        supabase_url: Supabase project URL
        supabase_service_key: Supabase service role key
    """
    headers = {
        "apikey": supabase_service_key,
        "Authorization": f"Bearer {supabase_service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    count = 0
    for opp in opps:
        try:
            liquidity = opp.get("liquidity")
            if not liquidity:
                continue

            position = compute_position_from_book_levels(liquidity)
            if position is None:
                continue
            if position["total_cost_usd"] < 5.0:
                continue

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
                    select_url = (
                        f"{supabase_url}/rest/v1/paper_trades"
                        f"?city=eq.{city_enc}&date=eq.{date_enc}"
                        f"&band_c=eq.{band_enc}&side=eq.{side_enc}"
                        f"&select=*&status=eq.open"
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

        # Check if market is closed/resolved
        if not market.get("closed", False):
            return None

        # Determine winning outcome from outcomePrices
        prices_raw = market.get("outcomePrices", "")
        try:
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        except (json.JSONDecodeError, TypeError):
            prices = None

        if prices and len(prices) >= 2:
            yes_price = float(prices[0])
            no_price = float(prices[1])
            # Resolved markets have prices at 1.0/0.0
            if yes_price > 0.9:
                return "YES"
            elif no_price > 0.9:
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

    # Fetch open trades with date < today
    query_url = (
        f"{supabase_url}/rest/v1/paper_trades"
        f"?status=eq.open&date=lt.{today_str}&select=*"
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
            condition_id = trade.get("condition_id", "")
            side = trade.get("side", "").upper()

            if not condition_id or not side:
                skipped += 1
                continue

            # Check cache first, then Polymarket API
            if condition_id not in resolution_cache:
                resolution_cache[condition_id] = check_polymarket_resolution(condition_id)

            winning_side = resolution_cache[condition_id]
            if winning_side is None:
                skipped += 1
                continue

            # Determine outcome: did our side win?
            outcome = "won" if side == winning_side else "lost"

            total_cost = float(trade.get("total_cost_usd", 0))
            total_shares = float(trade.get("total_shares", 0))

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
