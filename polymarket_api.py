"""
Polymarket API module — fetches temperature events and parses market data.
"""
import re
import json
import requests
from datetime import datetime
from dateutil import parser as dateparser
from config import (
    CITY_GEO, TOMORROW, TIER1_ONLY, JSON_OUT,
    TOMORROW_DATE, TOMORROW_STR, MAX_DATE,
    normalize_city, get_city_tier, dprint,
)


def fetch_temperature_events():
    """Fetch active temperature events from the Gamma events API."""
    if not JSON_OUT:
        print("Fetching Polymarket temperature events...", end="", flush=True)
    events = []
    try:
        r = requests.get("https://gamma-api.polymarket.com/events",
            params={"active": "true", "closed": "false", "tag_slug": "weather", "limit": 100},
            timeout=15)
        r.raise_for_status()
        raw = r.json()
        all_events = raw if isinstance(raw, list) else raw.get("data", [])
        for e in all_events:
            title = e.get("title", "")
            if "highest temperature" in title.lower():
                events.append(e)
    except Exception as ex:
        if not JSON_OUT:
            print(f"\n  [WARN] Gamma events API: {ex}")

    if not JSON_OUT:
        print(f" {len(events)} temperature events found.")
    return events


def parse_event_title(title):
    """Parse 'Highest temperature in Seoul on March 23?' -> (city, date_key) or None."""
    m = re.search(r'[Hh]ighest temperature in (.+?) on (\w+ \d+)', title)
    if not m:
        return None
    city_raw = m.group(1).strip()
    date_raw = m.group(2).strip()

    city = normalize_city(city_raw)
    if city not in CITY_GEO:
        dprint(f"SKIP unknown city '{city_raw}' (normalized: '{city}'): {title[:60]}")
        return None

    if TIER1_ONLY and get_city_tier(city) != 1:
        dprint(f"SKIP non-tier1 city '{city}': {title[:60]}")
        return None

    try:
        date_str = f"{date_raw} {datetime.now().year}"
        parsed = dateparser.parse(date_str)
        if parsed is None:
            return None
        if parsed.date() < TOMORROW_DATE:
            dprint(f"SKIP today/past {parsed.date()}: {title[:60]}")
            return None
        if parsed.date() > MAX_DATE:
            dprint(f"SKIP out of range {parsed.date()}: {title[:60]}")
            return None
        date_key = parsed.strftime("%Y-%m-%d")
        if TOMORROW and date_key != TOMORROW_STR:
            dprint(f"SKIP not tomorrow ({date_key}): {title[:60]}")
            return None
        return city, date_key
    except Exception:
        return None


def parse_group_item(title, is_fahrenheit):
    """Parse groupItemTitle like '15C', '6C or below', '16C or higher' -> (lo, hi, band_type).
    Returns temperatures in Celsius."""
    title = title.strip()

    m = re.match(r'(-?\d+)\s*°[CF]\s+or\s+below', title, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        if is_fahrenheit:
            val = (val - 32) * 5 / 9
        return val, val, "below"

    m = re.match(r'(-?\d+)\s*°[CF]\s+or\s+higher', title, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        if is_fahrenheit:
            val = (val - 32) * 5 / 9
        return val, val, "above"

    m = re.match(r'(-?\d+)\s*°[CF]$', title, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        if is_fahrenheit:
            val = (val - 32) * 5 / 9
        return val, val + 1.0, "exact"

    m = re.match(r'(-?\d+)\s*[-\u2013]\s*(-?\d+)\s*°[CF]', title, re.IGNORECASE)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if is_fahrenheit:
            lo = (lo - 32) * 5 / 9
            hi = (hi - 32) * 5 / 9
        return lo, hi, "exact"

    return None


def get_market_price(market):
    """Get YES and NO prices from Gamma market data."""
    prices_raw = market.get("outcomePrices", "")
    try:
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        if prices and len(prices) >= 2:
            yes_p = float(prices[0])
            no_p = float(prices[1])
            return yes_p, no_p, "live"
    except Exception:
        pass

    best_ask = market.get("bestAsk")
    if best_ask:
        try:
            yes_p = float(best_ask)
            return yes_p, 1.0 - yes_p, "bestAsk"
        except Exception:
            pass

    return None, None, None


def fetch_order_book(token_id):
    """Fetch order book from Polymarket CLOB API.
    Returns: {"bids": [(price, size), ...], "asks": [(price, size), ...]}
    sorted by price (bids descending, asks ascending).
    Returns None on failure.
    """
    if not token_id:
        return None
    try:
        from config import CLOB_API_BASE, CLOB_TIMEOUT
        r = requests.get(f"{CLOB_API_BASE}/book",
                        params={"token_id": token_id},
                        timeout=CLOB_TIMEOUT)
        r.raise_for_status()
        data = r.json()

        bids = []
        for b in data.get("bids", []):
            try:
                bids.append((float(b["price"]), float(b["size"])))
            except (KeyError, ValueError):
                continue

        asks = []
        for a in data.get("asks", []):
            try:
                asks.append((float(a["price"]), float(a["size"])))
            except (KeyError, ValueError):
                continue

        # Sort: bids highest first, asks lowest first
        bids.sort(key=lambda x: -x[0])
        asks.sort(key=lambda x: x[0])

        return {"bids": bids, "asks": asks}
    except Exception as e:
        dprint(f"  [WARN] CLOB book error: {e}")
        return None


def get_market_liquidity(market):
    """Extract liquidity and volume from Gamma market data.
    Returns: (liquidity_usd, volume_usd) or (0, 0) on failure.
    """
    try:
        liq = float(market.get("liquidity", 0) or 0)
        vol = float(market.get("volume", 0) or 0)
        return liq, vol
    except (ValueError, TypeError):
        return 0.0, 0.0
