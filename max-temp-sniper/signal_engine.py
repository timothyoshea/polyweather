"""
Max Temp Sniper — Signal Engine.
Checks which bands are locked when temperature rises. Outputs LockedBand list.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

from models import Band, Market, LockedBand, TriggerResult

logger = logging.getLogger("sniper.signal")


class SignalEngine:
    """Evaluates METAR readings against market bands to produce trade signals."""

    def __init__(self, markets: list[Market]):
        self.markets = markets

    def update_markets(self, markets: list[Market]):
        """Update the market list (called after scanner refresh)."""
        self.markets = markets
        logger.info(f"Signal engine updated with {len(markets)} markets")

    def evaluate(self, metar: dict) -> TriggerResult:
        """
        Evaluate a METAR reading against all market bands.

        Signal logic (strict >):
        - if temp > top_band_threshold -> lock top band YES
        - for each lower band where temp > band.temp_value -> lock as NO
        """
        temp = metar["temp"]
        raw = metar["raw"]
        station = metar["station"]
        previous_temp = metar.get("previous_temp")

        result = TriggerResult(
            station=station,
            metar_raw=raw,
            temp_observed=temp,
            previous_temp=previous_temp,
            signal_time=datetime.now(timezone.utc),
        )

        if not metar.get("is_new"):
            return result

        if not metar.get("is_rising"):
            logger.debug(f"Temp not rising ({temp}°C), no signals")
            return result

        for market in self.markets:
            locked = self._evaluate_market(market, temp)
            result.locked_bands.extend(locked)

        if result.has_signal:
            logger.info(
                f"SIGNAL: {temp}°C triggers {len(result.locked_bands)} bands "
                f"across {len(self.markets)} markets"
            )
        else:
            logger.debug(f"No bands locked at {temp}°C")

        return result

    def _evaluate_market(self, market: Market, temp: float) -> list[LockedBand]:
        """Evaluate a single market's bands against the observed temperature."""
        locked = []

        # 1. Top band YES: if temp exceeds top band threshold
        top = market.top_band
        if top and temp > top.temp_value:
            locked.append(LockedBand(
                band=top,
                market=market,
                side="YES",
                trade_type="top_band_yes",
                temp_observed=temp,
            ))
            logger.info(
                f"  TOP BAND YES: {top.label} (threshold {top.temp_value}°C, "
                f"observed {temp}°C) in {market.question}"
            )

        # 2. Lower band NO sweep: for each non-top band that temp exceeds
        for band in market.bands:
            if band.is_top_band:
                continue
            if temp > band.temp_value:
                locked.append(LockedBand(
                    band=band,
                    market=market,
                    side="NO",
                    trade_type="lower_band_no",
                    temp_observed=temp,
                ))
                logger.info(
                    f"  LOWER BAND NO: {band.label} (threshold {band.temp_value}°C, "
                    f"observed {temp}°C) in {market.question}"
                )

        return locked
