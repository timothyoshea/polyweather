"""
Max Temp Sniper — Market Scanner.
Polls Polymarket Gamma API /events endpoint for ALL temperature events,
extracts the ICAO station from the resolution source, builds band maps.
"""
from __future__ import annotations
import json
import logging
import re
import urllib.request
from typing import Optional

from models import Band, Market

logger = logging.getLogger("sniper.scanner")

GAMMA_EVENTS_URL = (
    "https://gamma-api.polymarket.com/events"
    "?active=true&closed=false&tag_slug=temperature&limit=200"
)

# Patterns to detect the top band ("25°C or higher", "≥25°C", "above 25")
TOP_BAND_PATTERNS = re.compile(r"(or higher|or more|or above|above|\+|≥|and above)", re.IGNORECASE)
# Patterns to detect the bottom band ("15°C or below", "≤15°C")
BOTTOM_BAND_PATTERNS = re.compile(r"(or below|or less|or lower|below|≤)", re.IGNORECASE)

# Extract numeric temperature and unit from text
TEMP_EXTRACT = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?\s*([CcFf])?")
# Detect Fahrenheit in question text
FAHRENHEIT_PATTERN = re.compile(r"°\s*F\b|fahrenheit", re.IGNORECASE)

# Fallback ICAO station mapping for cities where regex extraction fails
FALLBACK_STATIONS = {
    "Hong Kong": "VHHH",
    "Tel Aviv": "LLBG",
    "Istanbul": "LTFM",
    "Moscow": "UUEE",
}

# Fallback resolution sources for cities missing from Gamma API
FALLBACK_RESOLUTION_SOURCES = {
    "VHHH": "https://www.wunderground.com/history/daily/hk/hong-kong/VHHH",
    "LLBG": "https://www.wunderground.com/history/daily/il/tel-aviv/LLBG",
    "LTFM": "https://www.wunderground.com/history/daily/tr/istanbul/LTFM",
    "UUEE": "https://www.wunderground.com/history/daily/ru/moscow/UUEE",
}


def fetch_all_markets() -> list[Market]:
    """Fetch ALL active temperature events from Gamma API, build band maps."""
    try:
        req = urllib.request.Request(GAMMA_EVENTS_URL, headers={"User-Agent": "MaxTempSniper/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            events = json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"Gamma API fetch failed: {e}")
        return []

    markets = []
    for event in events:
        title = event.get("title", "")
        if "highest" not in title.lower():
            continue

        market = _parse_event(event)
        if market and market.bands:
            markets.append(market)
            top = market.top_band
            if top:
                logger.info(
                    f"Found: {market.city} ({market.station}) | "
                    f"{len(market.bands)} bands | top: {top.label} ({top.temp_value}°C)"
                )
            else:
                logger.info(
                    f"Found: {market.city} ({market.station}) | "
                    f"{len(market.bands)} bands | no top band detected"
                )

    logger.info(f"Scanner found {len(markets)} temperature markets across {len(set(m.station for m in markets))} stations")
    return markets


def _extract_city(title: str) -> str:
    """Extract city name from event title like 'Highest temperature in London on April 9?'"""
    m = re.search(r"in (.+?) on", title)
    return m.group(1) if m else ""


def _extract_market_date(title: str) -> str:
    """Extract ISO date from event title like 'Highest temperature in London on April 9?'
    Returns e.g. '2026-04-09' or '' if unparseable."""
    MONTHS = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    m = re.search(r"on (\w+) (\d+)", title, re.IGNORECASE)
    if not m:
        return ""
    month_name = m.group(1).lower()
    day = int(m.group(2))
    month_num = MONTHS.get(month_name)
    if not month_num:
        return ""
    # Assume current year
    from datetime import date
    year = date.today().year
    return f"{year}-{month_num:02d}-{day:02d}"


def _extract_station(event: dict, city: str) -> str:
    """Extract ICAO station code from resolution source URL or description."""
    for text in [event.get("resolutionSource", ""), event.get("description", "")]:
        # Match ICAO code at end of WU URL: .../EGLC or .../KLGA
        match = re.search(r"/([A-Z]{4})(?:\s|\.|$|/|\?|\\)", text)
        if match:
            return match.group(1)

    # Fallback mapping for cities where URL extraction fails
    return FALLBACK_STATIONS.get(city, "")


def _parse_event(event: dict) -> Optional[Market]:
    """Parse a Gamma event with nested markets into a Market with Bands."""
    try:
        event_markets = event.get("markets", [])
        if not event_markets:
            return None

        title = event.get("title", "")
        city = _extract_city(title)
        station = _extract_station(event, city)
        market_date = _extract_market_date(title)

        if not station:
            logger.warning(f"No METAR station found for {city}, skipping")
            return None

        market = Market(
            condition_id=event.get("id", ""),
            question=title,
            slug=event.get("slug", ""),
            end_date=event.get("endDate", ""),
            neg_risk_market_id=event.get("negRiskMarketID", ""),
            city=city,
            station=station,
            resolution_source=event.get("resolutionSource", "") or FALLBACK_RESOLUTION_SOURCES.get(station, ""),
        )

        for m in event_markets:
            question = m.get("question", "")

            # Parse clobTokenIds — JSON string: [yes_token, no_token]
            tokens_raw = m.get("clobTokenIds", "[]")
            if isinstance(tokens_raw, str):
                tokens = json.loads(tokens_raw)
            else:
                tokens = tokens_raw

            yes_token = tokens[0] if len(tokens) > 0 else ""
            no_token = tokens[1] if len(tokens) > 1 else ""
            condition_id = m.get("conditionId", "")

            band = _parse_band_from_question(question, yes_token, no_token, condition_id)
            if band:
                market.bands.append(band)

        # Sort bands by temperature ascending
        market.bands.sort(key=lambda b: b.temp_value)

        return market

    except Exception as e:
        logger.warning(f"Failed to parse event {event.get('title', '?')}: {e}")
        return None


def _parse_band_from_question(
    question: str, yes_token_id: str, no_token_id: str, condition_id: str = ""
) -> Optional[Band]:
    """Parse a band from a market question."""
    temp_match = TEMP_EXTRACT.search(question)
    if not temp_match:
        return None

    temp_value = float(temp_match.group(1))
    is_top = bool(TOP_BAND_PATTERNS.search(question))
    is_bottom = bool(BOTTOM_BAND_PATTERNS.search(question))

    # Detect unit from the question text
    unit = "F" if FAHRENHEIT_PATTERN.search(question) else "C"

    return Band(
        label=_short_label(temp_value, is_top, is_bottom, unit),
        temp_value=temp_value,
        is_top_band=is_top,
        is_bottom_band=is_bottom,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        condition_id=condition_id,
        unit=unit,
    )


def _short_label(temp: float, is_top: bool, is_bottom: bool, unit: str = "C") -> str:
    """Create a short band label like '22°C' or '76°F or higher'."""
    t = int(temp) if temp == int(temp) else temp
    u = f"°{unit}"
    if is_top:
        return f"{t}{u} or higher"
    if is_bottom:
        return f"{t}{u} or below"
    return f"{t}{u}"
