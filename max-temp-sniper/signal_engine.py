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

    def evaluate_market(self, metar: dict, market: Market) -> TriggerResult:
        """
        Evaluate a single METAR reading against a specific market's bands.
        Called per-market from main loop after a rising trigger is detected.

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

        locked = self._evaluate_market(market, temp)
        result.locked_bands.extend(locked)

        if result.has_signal:
            logger.info(
                f"SIGNAL {market.city}: {temp}°C triggers {len(result.locked_bands)} bands"
            )

        return result

    @staticmethod
    def _c_to_f(temp_c: float) -> float:
        """Convert Celsius to Fahrenheit."""
        return temp_c * 9 / 5 + 32

    def _evaluate_market(self, market: Market, temp_c: float) -> list[LockedBand]:
        """Evaluate a single market's bands against the observed temperature.

        temp_c is always in Celsius (from METAR). If bands are in °F,
        we convert the METAR temp to °F for comparison.
        """
        locked = []

        # Determine comparison temp based on market's unit
        # All bands in a market use the same unit
        # WU resolves to WHOLE degrees — we must round to match
        sample_band = market.bands[0] if market.bands else None
        if sample_band and sample_band.unit == "F":
            temp_f_raw = self._c_to_f(temp_c)
            temp_compare = round(temp_f_raw)  # WU rounds to whole °F
            unit_label = "°F"
        else:
            temp_compare = round(temp_c)  # WU rounds to whole °C
            unit_label = "°C"

        # 1. Top band YES: if rounded temp >= top band threshold
        # (WU shows whole degrees, so rounded temp == threshold means it hit)
        top = market.top_band
        if top and temp_compare >= top.temp_value:
            locked.append(LockedBand(
                band=top,
                market=market,
                side="YES",
                trade_type="top_band_yes",
                temp_observed=temp_c,
            ))
            logger.info(
                f"  TOP BAND YES: {top.label} (observed {temp_compare:.1f}{unit_label}) "
                f"in {market.city}"
            )

        # 2. Lower band NO sweep: for each non-top, non-bottom band where the
        # rounded WU temp is ABOVE the band's range, confirming it's a loser.
        # For "17°C" band: loser when rounded temp > 17 (i.e., >= 18)
        # For "38-39°F" band: loser when rounded temp > 39 (i.e., >= 40)
        for band in market.bands:
            if band.is_top_band or band.is_bottom_band:
                continue
            high = band.temp_value_high if band.temp_value_high else band.temp_value
            if temp_compare > high:
                locked.append(LockedBand(
                    band=band,
                    market=market,
                    side="NO",
                    trade_type="lower_band_no",
                    temp_observed=temp_c,
                ))
                logger.debug(
                    f"  LOWER BAND NO: {band.label} (observed {temp_compare:.1f}{unit_label}) "
                    f"in {market.city}"
                )

        return locked
