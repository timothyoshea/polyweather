"""
Max Temp Sniper — Multi-Station METAR Poller.
Polls aviationweather.gov for ALL active stations every 10 seconds.
Batches stations into a single API call where possible.
Logs every new METAR observation to Supabase for historical tracking.
"""
from __future__ import annotations
import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from market_scanner import ALTERNATIVE_FEEDS

logger = logging.getLogger("sniper.metar")

METAR_API = "https://aviationweather.gov/api/data/metar"

# Supabase config (module-level, read once)
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# Weather phenomena codes to look for in METAR
_WEATHER_CODES = {
    "CAVOK", "NCD", "NSC", "SKC", "CLR", "FEW", "SCT", "BKN", "OVC",
    "RA", "SN", "DZ", "GR", "GS", "SG", "IC", "PL", "UP",
    "FG", "BR", "HZ", "FU", "SA", "DU", "VA",
    "TS", "SQ", "FC", "DS", "SS",
    "+RA", "-RA", "+SN", "-SN", "+DZ", "-DZ", "+TS", "-TS",
    "TSRA", "+TSRA", "-TSRA", "VCSH", "VCTS",
    "SHRA", "+SHRA", "-SHRA", "SHSN", "+SHSN", "-SHSN",
    "FZRA", "+FZRA", "-FZRA", "FZDZ", "FZFG",
    "BCFG", "PRFG", "MIFG",
}


def _parse_metar_fields(raw: str) -> dict:
    """
    Extract observation_time, wind, visibility, and weather from a raw METAR string.

    Example: "METAR EHAM 081855Z 13005KT 100V160 CAVOK 15/03 Q1023 NOSIG"
      - observation_time = "081855Z"
      - wind = "13005KT"
      - visibility = "CAVOK"
      - weather = "CAVOK"
    """
    result = {
        "observation_time": None,
        "wind": None,
        "visibility": None,
        "weather": None,
    }

    tokens = raw.strip().split()
    if not tokens:
        return result

    # Find the observation time token (format: DDHHMMz, e.g. "081855Z")
    for i, tok in enumerate(tokens):
        if re.match(r"^\d{6}Z$", tok):
            result["observation_time"] = tok
            break

    # Find wind token (ends with KT or MPS, e.g. "13005KT", "VRB02KT")
    for tok in tokens:
        if re.match(r"^(VRB|\d{3})\d{2,3}(G\d{2,3})?(KT|MPS)$", tok):
            result["wind"] = tok
            break

    # Find visibility: numeric token (e.g. "9999", "0800") or "CAVOK" after wind
    wind_found = False
    for tok in tokens:
        if result["wind"] and tok == result["wind"]:
            wind_found = True
            continue
        if not wind_found:
            continue
        # Skip variable wind direction (e.g. "100V160")
        if re.match(r"^\d{3}V\d{3}$", tok):
            continue
        if tok == "CAVOK":
            result["visibility"] = "CAVOK"
            break
        if re.match(r"^\d{4}$", tok):
            result["visibility"] = tok
            break
        if re.match(r"^\d+SM$", tok):
            result["visibility"] = tok
            break
        # If we hit temp/dew (NN/NN) or QNH, stop looking
        if re.match(r"^M?\d{2}/M?\d{2}$", tok) or tok.startswith("Q") or tok.startswith("A"):
            break

    # Find weather phenomena
    weather_parts = []
    for tok in tokens:
        # Check exact match or prefix-stripped match
        if tok in _WEATHER_CODES:
            weather_parts.append(tok)
        elif tok == "CAVOK":
            weather_parts.append("CAVOK")
    result["weather"] = " ".join(weather_parts) if weather_parts else None

    return result


class MetarPoller:
    """Polls METAR data for multiple stations and tracks temperature changes per station."""

    def __init__(self):
        # Per-station state: {station: {last_raw, last_temp, previous_temp}}
        self._state: dict[str, dict] = {}
        # Station metadata: {station: {"city": "...", "resolution_source": "..."}}
        self._station_metadata: dict[str, dict] = {}

    def set_station_metadata(self, mapping: dict):
        """
        Set station-to-city metadata mapping.
        Called from main.py after market scanner runs.

        Args:
            mapping: dict of {station: {"city": "Amsterdam", "resolution_source": "https://..."}}
        """
        self._station_metadata = mapping
        logger.info(f"Station metadata set for {len(mapping)} stations")

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
        previous_temp = state["last_temp"]
        state["previous_temp"] = previous_temp
        state["last_temp"] = temp
        state["last_raw"] = raw_ob

        # Freshness check — reject stale observations
        obs_time_str = obs.get("reportTime") or obs.get("obsTime")  # API may provide this
        if obs_time_str:
            try:
                from datetime import datetime, timezone
                # obsTime format varies, try common formats
                obs_dt = datetime.fromisoformat(obs_time_str.replace("Z", "+00:00"))
                age_seconds = (datetime.now(timezone.utc) - obs_dt).total_seconds()
                if age_seconds > 600:  # 10 minutes
                    logger.warning(f"Stale METAR for {station}: {age_seconds:.0f}s old, skipping")
                    return None
            except Exception:
                pass  # If we can't parse time, continue anyway

        is_rising = (
            previous_temp is not None
            and temp > previous_temp
        )

        # Log EVERY new observation to Supabase (not just rising)
        self._log_reading_to_supabase(station, temp, raw_ob, previous_temp, is_rising)

        if is_rising:
            logger.info(
                f"RISING {station}: {temp}°C (was {previous_temp}°C) | {raw_ob}"
            )
            return {
                "temp": temp,
                "raw": raw_ob,
                "station": station,
                "previous_temp": previous_temp,
            }
        else:
            logger.debug(
                f"{station}: {temp}°C (prev: {previous_temp}°C) stable/falling"
            )
            return None

    def _log_reading_to_supabase(
        self, station: str, temp_c: float, raw: str,
        previous_temp: Optional[float], is_rising: bool
    ):
        """Best-effort insert of every METAR reading into metar_readings table."""
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            return

        try:
            # Get metadata for this station
            meta = self._station_metadata.get(station, {})
            city = meta.get("city")
            resolution_source = meta.get("resolution_source")

            # Parse METAR fields
            parsed = _parse_metar_fields(raw)

            # Calculate derived fields
            temp_f = round(temp_c * 9 / 5 + 32, 2)
            temp_change = round(temp_c - previous_temp, 2) if previous_temp is not None else None

            payload = json.dumps({
                "station": station,
                "city": city,
                "temp_c": temp_c,
                "temp_f": temp_f,
                "metar_raw": raw,
                "observation_time": parsed["observation_time"],
                "wind": parsed["wind"],
                "visibility": parsed["visibility"],
                "weather": parsed["weather"],
                "previous_temp_c": previous_temp,
                "temp_change": temp_change,
                "is_rising": is_rising,
                "resolution_source": resolution_source,
            }).encode()

            url = f"{SUPABASE_URL}/rest/v1/metar_readings"
            req = urllib.request.Request(
                url,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Prefer": "return=minimal",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.debug(f"METAR reading logged for {station} ({resp.status})")
        except Exception as e:
            logger.warning(f"Failed to log METAR reading for {station}: {e}")

    def poll_alternative_stations(self, stations: list[str]) -> list[dict]:
        """
        Poll alternative temperature feeds for stations without reliable METAR.
        Returns list of rising trigger dicts in the same format as poll_all().
        """
        triggers = []
        for station in stations:
            feed = ALTERNATIVE_FEEDS.get(station)
            if not feed:
                continue
            try:
                if feed["type"] == "hko":
                    result = self._poll_hko(station, feed)
                elif feed["type"] == "open_meteo":
                    result = self._poll_open_meteo(station, feed)
                else:
                    logger.warning(f"Unknown alt feed type for {station}: {feed['type']}")
                    continue

                if result:
                    triggers.append(result)
            except Exception as e:
                logger.warning(f"Alternative feed failed for {station}: {e}")
        return triggers

    def _poll_hko(self, station: str, feed: dict) -> Optional[dict]:
        """Poll Hong Kong Observatory API for current temperature."""
        url = feed["url"]
        target_place = feed["station_name"]

        req = urllib.request.Request(url, headers={"User-Agent": "MaxTempSniper/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        # Find target station in temperature data array
        temp_data = data.get("temperature", {}).get("data", [])
        obs = None
        for item in temp_data:
            if item.get("place") == target_place:
                obs = item
                break

        if obs is None:
            logger.debug(f"HKO: station '{target_place}' not found in response")
            return None

        temp = float(obs["value"])
        record_time = data.get("temperature", {}).get("recordTime", "")
        raw = f"HKO API: {temp}\u00b0C at {record_time}"

        return self._process_alt_observation(station, temp, raw, record_time)

    def _poll_open_meteo(self, station: str, feed: dict) -> Optional[dict]:
        """Poll Open-Meteo API for current temperature."""
        lat = feed["latitude"]
        lng = feed["longitude"]
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current_weather=true"

        req = urllib.request.Request(url, headers={"User-Agent": "MaxTempSniper/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        cw = data.get("current_weather", {})
        temp = float(cw["temperature"])
        obs_time = cw.get("time", "")
        raw = f"Open-Meteo: {temp}\u00b0C at {obs_time}"

        return self._process_alt_observation(station, temp, raw, obs_time)

    def _process_alt_observation(
        self, station: str, temp: float, raw: str, obs_time: str
    ) -> Optional[dict]:
        """Dedup and detect rising for an alternative feed observation."""
        self._ensure_station(station)
        state = self._state[station]

        # Dedup by observation time string (stored in last_raw)
        if obs_time == state["last_raw"]:
            return None

        previous_temp = state["last_temp"]
        state["previous_temp"] = previous_temp
        state["last_temp"] = temp
        state["last_raw"] = obs_time

        is_rising = previous_temp is not None and temp > previous_temp

        self._log_reading_to_supabase(station, temp, raw, previous_temp, is_rising)

        if is_rising:
            logger.info(f"RISING {station} (alt): {temp}\u00b0C (was {previous_temp}\u00b0C) | {raw}")
            return {
                "temp": temp,
                "raw": raw,
                "station": station,
                "previous_temp": previous_temp,
            }
        else:
            logger.debug(f"{station} (alt): {temp}\u00b0C (prev: {previous_temp}\u00b0C) stable/falling")
            return None

    def get_temp(self, station: str) -> Optional[float]:
        """Get the last known temperature for a station."""
        state = self._state.get(station)
        return state["last_temp"] if state else None
