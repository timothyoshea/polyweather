"""
Max Temp Sniper — Multi-Station METAR Poller.
Polls aviationweather.gov for ALL active stations every 10 seconds.
Batches stations into a single API call where possible.
"""
from __future__ import annotations
import json
import logging
import urllib.request
from typing import Optional

logger = logging.getLogger("sniper.metar")

METAR_API = "https://aviationweather.gov/api/data/metar"


class MetarPoller:
    """Polls METAR data for multiple stations and tracks temperature changes per station."""

    def __init__(self):
        # Per-station state: {station: {last_raw, last_temp, previous_temp}}
        self._state: dict[str, dict] = {}

    def _ensure_station(self, station: str):
        if station not in self._state:
            self._state[station] = {
                "last_raw": None,
                "last_temp": None,
                "previous_temp": None,
            }

    def poll_all(self, stations: list[str]) -> list[dict]:
        """
        Fetch METAR for all stations in one batch call.
        Returns list of result dicts for stations with NEW RISING temperatures.
        Each dict: {temp, raw, station, previous_temp}
        """
        if not stations:
            return []

        # Deduplicate stations
        unique_stations = list(set(stations))

        # Batch fetch — aviationweather.gov supports comma-separated station IDs
        results = []
        # Batch in groups of 20 to avoid URL length issues
        for i in range(0, len(unique_stations), 20):
            batch = unique_stations[i:i + 20]
            batch_results = self._fetch_batch(batch)
            results.extend(batch_results)

        return results

    def _fetch_batch(self, stations: list[str]) -> list[dict]:
        """Fetch METAR for a batch of stations, return rising triggers."""
        station_str = ",".join(stations)
        url = f"{METAR_API}?ids={station_str}&format=json&hours=1"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MaxTempSniper/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            logger.error(f"METAR batch fetch failed for {station_str}: {e}")
            return []

        if not data or not isinstance(data, list):
            return []

        # Group observations by station (API may return multiple per station)
        by_station: dict[str, dict] = {}
        for obs in data:
            sid = obs.get("icaoId", "")
            if sid and sid not in by_station:
                by_station[sid] = obs  # first = most recent

        triggers = []
        for station in stations:
            obs = by_station.get(station)
            if not obs:
                continue

            result = self._process_observation(station, obs)
            if result:
                triggers.append(result)

        return triggers

    def _process_observation(self, station: str, obs: dict) -> Optional[dict]:
        """Process a single METAR observation. Returns trigger dict if rising, else None."""
        self._ensure_station(station)
        state = self._state[station]

        raw_ob = obs.get("rawOb", "")
        temp = obs.get("temp")

        if temp is None:
            return None

        temp = float(temp)

        # Dedup: skip if same raw observation
        if raw_ob == state["last_raw"]:
            return None

        # Track temperature changes
        state["previous_temp"] = state["last_temp"]
        state["last_temp"] = temp
        state["last_raw"] = raw_ob

        is_rising = (
            state["previous_temp"] is not None
            and temp > state["previous_temp"]
        )

        if is_rising:
            logger.info(
                f"RISING {station}: {temp}°C (was {state['previous_temp']}°C) | {raw_ob}"
            )
            return {
                "temp": temp,
                "raw": raw_ob,
                "station": station,
                "previous_temp": state["previous_temp"],
            }
        else:
            logger.debug(
                f"{station}: {temp}°C (prev: {state['previous_temp']}°C) stable/falling"
            )
            return None

    def get_temp(self, station: str) -> Optional[float]:
        """Get the last known temperature for a station."""
        state = self._state.get(station)
        return state["last_temp"] if state else None
