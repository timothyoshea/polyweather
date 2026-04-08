"""
Max Temp Sniper — METAR Poller.
Polls aviationweather.gov for EGLC station, detects rising temperature.
"""
from __future__ import annotations
import json
import logging
import urllib.request
from typing import Optional

logger = logging.getLogger("sniper.metar")

METAR_URL = "https://aviationweather.gov/api/data/metar?ids=EGLC&format=json&hours=1"


class MetarPoller:
    """Polls METAR data and tracks temperature changes."""

    def __init__(self, station: str = "EGLC"):
        self.station = station
        self.last_raw: Optional[str] = None
        self.last_temp: Optional[float] = None
        self.previous_temp: Optional[float] = None

    def poll(self) -> Optional[dict]:
        """
        Fetch latest METAR. Returns dict with keys:
            temp, raw, station, is_new, previous_temp, is_rising
        Returns None on error or if no new observation.
        """
        try:
            url = f"https://aviationweather.gov/api/data/metar?ids={self.station}&format=json&hours=1"
            req = urllib.request.Request(url, headers={"User-Agent": "MaxTempSniper/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            logger.error(f"METAR fetch failed: {e}")
            return None

        if not data or not isinstance(data, list) or len(data) == 0:
            logger.warning("METAR returned empty data")
            return None

        obs = data[0]
        raw_ob = obs.get("rawOb", "")
        temp = obs.get("temp")

        if temp is None:
            logger.warning(f"No temp in METAR: {raw_ob}")
            return None

        temp = float(temp)

        # Dedup: skip if same raw observation
        is_new = (raw_ob != self.last_raw)
        if not is_new:
            logger.debug(f"Same METAR obs, temp={temp}°C")
            return {
                "temp": temp,
                "raw": raw_ob,
                "station": self.station,
                "is_new": False,
                "previous_temp": self.last_temp,
                "is_rising": False,
            }

        # Track temperature changes
        self.previous_temp = self.last_temp
        self.last_temp = temp
        self.last_raw = raw_ob

        is_rising = (
            self.previous_temp is not None
            and temp > self.previous_temp
        )

        logger.info(
            f"New METAR: {temp}°C (prev: {self.previous_temp}°C) "
            f"{'RISING' if is_rising else 'stable/falling'} | {raw_ob}"
        )

        return {
            "temp": temp,
            "raw": raw_ob,
            "station": self.station,
            "is_new": True,
            "previous_temp": self.previous_temp,
            "is_rising": is_rising,
        }
