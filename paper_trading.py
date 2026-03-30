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
        price = lv.get("price", 0)
        if price > 0 and cost > 0:
            shares = cost / price
            total_cost += cost
            total_shares += shares

    if total_shares <= 0 or total_cost <= 0:
        return None

    return {
        "entry_price": round(total_cost / total_shares, 4),
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
                "question": opp.get("question"),
                "token_id": opp.get("token_id"),
                "condition_id": opp.get("condition_id"),
                "event_slug": opp.get("event_slug"),
                "market_slug": opp.get("market_slug"),
                "url": opp.get("url"),
                "status": "open",
            }

            # Try INSERT; on conflict (city, date, band_c, side), select existing
            trade_id = None
            try:
                insert_url = f"{supabase_url}/rest/v1/paper_trades"
                result = _supabase_request(insert_url, [trade_row], headers)
                if result and len(result) > 0:
                    trade_id = result[0].get("id")
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    # Conflict — row already exists, fetch it
                    city_enc = urllib.parse.quote(opp.get("city", ""))
                    date_enc = urllib.parse.quote(opp.get("date", ""))
                    band_enc = urllib.parse.quote(opp.get("band_c", ""))
                    side_enc = urllib.parse.quote(opp.get("side", ""))
                    select_url = (
                        f"{supabase_url}/rest/v1/paper_trades"
                        f"?city=eq.{city_enc}&date=eq.{date_enc}"
                        f"&band_c=eq.{band_enc}&side=eq.{side_enc}"
                        f"&select=id"
                    )
                    existing = _supabase_get(select_url, headers)
                    if existing and len(existing) > 0:
                        trade_id = existing[0].get("id")
                else:
                    raise

            # Insert a snapshot for this scan
            if trade_id:
                snapshot_row = {
                    "trade_id": trade_id,
                    "scan_id": scan_id,
                    "my_p": opp.get("my_p"),
                    "mkt_p": opp.get("mkt_p"),
                    "edge": opp.get("edge"),
                    "confidence": opp.get("confidence"),
                    "entry_price": position["entry_price"],
                    "total_cost_usd": position["total_cost_usd"],
                    "total_shares": position["total_shares"],
                    "num_levels": position["num_levels"],
                    "forecast_c": opp.get("forecast_c"),
                    "liquidity": liquidity,
                }
                snapshot_url = f"{supabase_url}/rest/v1/trade_snapshots"
                try:
                    _supabase_request(snapshot_url, [snapshot_row], headers)
                except Exception as snap_err:
                    print(f"[WARN] Snapshot insert error for trade {trade_id}: {snap_err}")

        except Exception as e:
            print(f"[WARN] Paper trade error for {opp.get('city')}/{opp.get('date')}: {e}")


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


def resolve_open_trades(supabase_url, supabase_service_key, city_geo):
    """Resolve open paper trades whose date has passed.

    Fetches actual temperatures from Open-Meteo and updates trade outcomes.

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

    # Group by (city, date)
    groups = {}
    for t in trades:
        key = (t.get("city"), t.get("date"))
        groups.setdefault(key, []).append(t)

    resolved = 0
    won = 0
    lost = 0
    skipped = 0

    for (city, date_str), group_trades in groups.items():
        actual_temp = fetch_actual_temperature(city, date_str, city_geo)
        if actual_temp is None:
            skipped += len(group_trades)
            continue

        for trade in group_trades:
            try:
                outcome = determine_outcome(
                    actual_temp,
                    trade.get("band_c", ""),
                    trade.get("band_type", ""),
                    trade.get("side", ""),
                )

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

                # Update trade via PATCH
                trade_id = trade.get("id")
                update_url = (
                    f"{supabase_url}/rest/v1/paper_trades"
                    f"?id=eq.{trade_id}"
                )
                update_data = {
                    "status": outcome,
                    "resolved_at": datetime.utcnow().isoformat() + "Z",
                    "actual_temp_c": round(actual_temp, 1),
                    "payout_usd": round(payout, 2),
                    "profit_usd": round(profit, 2),
                    "roi_pct": round(roi_pct, 2),
                }
                _supabase_request(update_url, update_data, headers, method="PATCH")
                resolved += 1

            except Exception as e:
                print(f"[WARN] Error resolving trade {trade.get('id')}: {e}")
                skipped += 1

    return {"resolved": resolved, "won": won, "lost": lost, "skipped": skipped}
