"""
Max Temp Sniper — Data models.
Dataclasses for bands, markets, positions, trades, signals, and trigger results.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Band:
    """A single temperature band in a market (e.g. '25°C or higher')."""
    label: str              # e.g. "25°C or higher"
    temp_value: float       # numeric threshold in market's native unit (°C or °F)
    is_top_band: bool       # True if this is the highest band ("or higher" / "above" / "+")
    yes_token_id: str       # CLOB token ID for YES
    no_token_id: str        # CLOB token ID for NO
    is_bottom_band: bool = False  # True if this is the lowest band ("or below")
    condition_id: str = ""  # Polymarket conditionId for this specific band/market
    unit: str = "C"         # "C" or "F" — the unit used in the market


@dataclass
class Market:
    """A Polymarket temperature event with its bands."""
    condition_id: str       # Polymarket event ID
    question: str           # Full event title
    slug: str               # URL slug
    end_date: str           # Market close date
    neg_risk_market_id: str = ""  # negRisk market ID for the event
    city: str = ""          # City name (e.g. "London")
    station: str = ""       # ICAO METAR station (e.g. "EGLC")
    resolution_source: str = ""  # Resolution source URL (e.g. Weather Underground)
    bands: list[Band] = field(default_factory=list)

    @property
    def top_band(self) -> Optional[Band]:
        for b in self.bands:
            if b.is_top_band:
                return b
        return None

    def bands_below(self, temp: float) -> list[Band]:
        """Return bands whose threshold the observed temp has exceeded (strict >).
        Excludes bottom band ("or below") and top band."""
        return [b for b in self.bands
                if not b.is_top_band and not b.is_bottom_band and temp > b.temp_value]


@dataclass
class LockedBand:
    """A band that has been locked by a temperature trigger."""
    band: Band
    market: Market
    side: str               # "YES" or "NO"
    trade_type: str         # "top_band_yes" or "lower_band_no"
    temp_observed: float    # the METAR temp that triggered this


@dataclass
class TriggerResult:
    """Output of the signal engine for a single METAR reading."""
    station: str
    metar_raw: str
    temp_observed: float
    previous_temp: Optional[float]
    signal_time: datetime
    locked_bands: list[LockedBand] = field(default_factory=list)

    @property
    def has_signal(self) -> bool:
        return len(self.locked_bands) > 0


@dataclass
class Position:
    """Tracks an open position to prevent double entry."""
    market_id: str
    band_label: str
    side: str               # "YES" or "NO"
    trade_type: str
    entry_price: float
    size_usdc: float
    created_at: str


@dataclass
class Trade:
    """A paper or live trade record."""
    id: Optional[str] = None
    signal_id: Optional[str] = None
    market_id: str = ""
    market_question: str = ""
    band_label: str = ""
    band_temp: float = 0.0
    side: str = ""
    trade_type: str = ""
    temp_observed: float = 0.0
    entry_price: float = 0.0
    size_usdc: float = 0.0
    status: str = "open"        # open / won / lost
    profit_usd: Optional[float] = None
    resolved_at: Optional[str] = None
    created_at: Optional[str] = None
