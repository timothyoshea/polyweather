"""
Max Temp Sniper — Market Scanner.
Polls Polymarket Gamma API /events endpoint for London temperature events,
builds band map from the individual markets within each event.
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
    "?active=true&closed=false&tag_slug=temperature&limit=100"
)

# Patterns to detect the top band ("25°C or higher", "≥25°C", "above 25")
TOP_BAND_PATTERNS = re.compile(r"(or higher|or more|or above|above|\+|≥|and above)", re.IGNORECASE)
# Patterns to detect the bottom band ("15°C or below", "≤15°C")
BOTTOM_BAND_PATTERNS = re.compile(r"(or below|or less|or lower|below|≤)", re.IGNORECASE)

# Extract numeric temperature from text
TEMP_EXTRACT = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?[CcFf]?")


def fetch_london_markets() -> list[Market]:
    """Fetch active London temperature events from Gamma API, build band maps."""
    try:
        req = urllib.request.Request(GAMMA_EVENTS_URL, headers={"User-Agent": "MaxTempSniper/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            events = json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"Gamma API fetch failed: {e}")
        return []

    markets = []
    for event in events:
        title = event.get("title", "")
        if "london" not in title.lower():
            continue
        if "highest" not in title.lower():
            continue

        market = _parse_event(event)
        if market and market.bands:
            markets.append(market)
            top = market.top_band
            logger.info(
                f"Found: {market.question} | {len(market.bands)} bands | "
                f"top: {top.label} ({top.temp_value}°C)" if top else
                f"Found: {market.question} | {len(market.bands)} bands | no top band"
            )

    logger.info(f"Scanner found {len(markets)} London temperature markets")
    return markets


def _parse_event(event: dict) -> Optional[Market]:
    """Parse a Gamma event with nested markets into a Market with Bands."""
    try:
        event_markets = event.get("markets", [])
        if not event_markets:
            return None

        market = Market(
            condition_id=event.get("id", ""),
            question=event.get("title", ""),
            slug=event.get("slug", ""),
            end_date=event.get("endDate", ""),
            neg_risk_market_id=event.get("negRiskMarketID", ""),
        )

        for m in event_markets:
            question = m.get("question", "")
            condition_id = m.get("conditionId", "")

            # Parse clobTokenIds — JSON string: [yes_token, no_token]
            tokens_raw = m.get("clobTokenIds", "[]")
            if isinstance(tokens_raw, str):
                tokens = json.loads(tokens_raw)
            else:
                tokens = tokens_raw

            yes_token = tokens[0] if len(tokens) > 0 else ""
            no_token = tokens[1] if len(tokens) > 1 else ""

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
    """Parse a band from a market question like 'Will the highest temperature in London be 22°C on April 9?'"""
    temp_match = TEMP_EXTRACT.search(question)
    if not temp_match:
        return None

    temp_value = float(temp_match.group(1))
    is_top = bool(TOP_BAND_PATTERNS.search(question))
    is_bottom = bool(BOTTOM_BAND_PATTERNS.search(question))

    return Band(
        label=_short_label(question, temp_value, is_top, is_bottom),
        temp_value=temp_value,
        is_top_band=is_top,
        is_bottom_band=is_bottom,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        condition_id=condition_id,
    )


def _short_label(question: str, temp: float, is_top: bool, is_bottom: bool) -> str:
    """Create a short band label like '22°C' or '25°C or higher'."""
    t = int(temp) if temp == int(temp) else temp
    if is_top:
        return f"{t}°C or higher"
    if is_bottom:
        return f"{t}°C or below"
    return f"{t}°C"
