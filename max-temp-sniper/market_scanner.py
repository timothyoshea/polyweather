"""
Max Temp Sniper — Market Scanner.
Polls Polymarket Gamma API for London temperature markets, builds band map with token IDs.
"""
from __future__ import annotations
import json
import logging
import re
import urllib.request
from typing import Optional

from models import Band, Market

logger = logging.getLogger("sniper.scanner")

GAMMA_URL = (
    "https://gamma-api.polymarket.com/markets"
    "?active=true&closed=false&tag_slug=temperature&limit=50"
)

# Patterns to detect the top band
TOP_BAND_PATTERNS = re.compile(r"(or higher|or more|above|\+|≥)", re.IGNORECASE)

# Extract numeric temperature from band label — matches e.g. "25°C", "25 °C", "25C", "25"
TEMP_EXTRACT = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?[CcFf]?")


def fetch_london_markets() -> list[Market]:
    """Fetch active London temperature markets from Gamma API."""
    try:
        req = urllib.request.Request(GAMMA_URL, headers={"User-Agent": "MaxTempSniper/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"Gamma API fetch failed: {e}")
        return []

    markets = []
    for item in data:
        question = item.get("question", "")
        # Filter for London markets
        if "london" not in question.lower():
            continue

        market = _parse_market(item)
        if market and market.bands:
            markets.append(market)
            logger.info(
                f"Found market: {market.question} with {len(market.bands)} bands "
                f"(top band: {market.top_band.label if market.top_band else 'none'})"
            )

    logger.info(f"Scanner found {len(markets)} London temperature markets")
    return markets


def _parse_market(item: dict) -> Optional[Market]:
    """Parse a single Gamma API market item into a Market with Bands."""
    try:
        outcomes_raw = item.get("outcomes", "[]")
        tokens_raw = item.get("clobTokenIds", "[]")

        # These can be JSON strings or already lists
        if isinstance(outcomes_raw, str):
            outcomes = json.loads(outcomes_raw)
        else:
            outcomes = outcomes_raw

        if isinstance(tokens_raw, str):
            tokens = json.loads(tokens_raw)
        else:
            tokens = tokens_raw

        # For binary markets: outcomes = ["Yes", "No"], tokens = [yes_id, no_id]
        # For multi-outcome: outcomes = ["24°C or lower", "25°C", ...], tokens = [id1, id2, ...]
        # In multi-outcome, each outcome has its own YES token; NO token is implicit

        market = Market(
            condition_id=item.get("conditionId", item.get("id", "")),
            question=item.get("question", ""),
            slug=item.get("slug", ""),
            end_date=item.get("endDate", item.get("endDateIso", "")),
        )

        if len(outcomes) == 2 and outcomes[0].lower() == "yes" and outcomes[1].lower() == "no":
            # Binary market — single band
            band = _make_band_from_question(
                item.get("question", ""),
                yes_token_id=tokens[0] if len(tokens) > 0 else "",
                no_token_id=tokens[1] if len(tokens) > 1 else "",
            )
            if band:
                market.bands.append(band)
        else:
            # Multi-outcome market — each outcome is a band
            for i, outcome_label in enumerate(outcomes):
                yes_token_id = tokens[i] if i < len(tokens) else ""
                band = _make_band(outcome_label, yes_token_id=yes_token_id)
                if band:
                    market.bands.append(band)

        # Sort bands by temperature (ascending)
        market.bands.sort(key=lambda b: b.temp_value)

        return market

    except Exception as e:
        logger.warning(f"Failed to parse market {item.get('question', '?')}: {e}")
        return None


def _make_band(label: str, yes_token_id: str = "", no_token_id: str = "") -> Optional[Band]:
    """Create a Band from an outcome label like '25°C or higher'."""
    temp_match = TEMP_EXTRACT.search(label)
    if not temp_match:
        return None

    temp_value = float(temp_match.group(1))
    is_top = bool(TOP_BAND_PATTERNS.search(label))

    return Band(
        label=label,
        temp_value=temp_value,
        is_top_band=is_top,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
    )


def _make_band_from_question(question: str, yes_token_id: str, no_token_id: str) -> Optional[Band]:
    """For binary markets, extract band info from the question text."""
    temp_match = TEMP_EXTRACT.search(question)
    if not temp_match:
        return None

    temp_value = float(temp_match.group(1))
    is_top = bool(TOP_BAND_PATTERNS.search(question))

    return Band(
        label=question,
        temp_value=temp_value,
        is_top_band=is_top,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
    )
