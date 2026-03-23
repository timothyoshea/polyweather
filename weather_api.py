"""
Weather API module — fetches multi-model deterministic + multi-model ensemble forecasts
from Open-Meteo. Uses 122 ensemble members across 3 models (ECMWF 51 + GFS 31 + ICON 40).

Handles rate limits gracefully: falls back to deterministic-only if ensemble API is throttled.
Uses concurrent requests to speed up fetching.
"""
import time
import requests
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import (
    FORECAST_DAYS, DETERMINISTIC_MODELS, CITY_GEO,
    dprint,
)

# Ensemble models to request and their internal API names + member counts
ENSEMBLE_MODELS = {
    "ecmwf_ifs025":  {"api_name": "ecmwf_ifs025_ensemble", "members": 50},
    "gfs_seamless":  {"api_name": "ncep_gefs_seamless",    "members": 30},
    "icon_seamless": {"api_name": "icon_seamless_eps",      "members": 39},
}

# Track if ensemble API is rate-limited so we don't keep hammering it
_ensemble_rate_limited = False


def fetch_deterministic_forecasts(lat, lng, timezone):
    """Fetch temperature_2m_max from multiple deterministic models.
    Returns: {date_str: {model_name: temp_c, ...}, ...}
    """
    models_str = ",".join(DETERMINISTIC_MODELS)
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat,
            "longitude": lng,
            "daily": "temperature_2m_max",
            "forecast_days": FORECAST_DAYS,
            "timezone": timezone,
            "models": models_str,
            "temperature_unit": "celsius",
        }, timeout=15)
        r.raise_for_status()
        resp = r.json()
        if resp.get("error"):
            dprint(f"  [WARN] Deterministic API error: {resp.get('reason', 'unknown')}")
            return {}
        data = resp.get("daily", {})
    except Exception as e:
        dprint(f"  [WARN] Deterministic API error: {e}")
        return {}

    dates = data.get("time", [])
    if not dates:
        return {}

    result = {}
    for i, date_str in enumerate(dates):
        model_temps = {}
        for model in DETERMINISTIC_MODELS:
            key = f"temperature_2m_max_{model}"
            vals = data.get(key, [])
            if i < len(vals) and vals[i] is not None:
                model_temps[model] = vals[i]
        if model_temps:
            result[date_str] = model_temps

    return result


def fetch_ensemble_forecasts(lat, lng, timezone):
    """Fetch multi-model ensemble for temperature_2m_max.
    Returns: {date_str: [temp_values...], ...}
    Returns empty dict if rate-limited (graceful fallback).
    """
    global _ensemble_rate_limited

    if _ensemble_rate_limited:
        return {}

    models_str = ",".join(ENSEMBLE_MODELS.keys())
    try:
        r = requests.get("https://ensemble-api.open-meteo.com/v1/ensemble", params={
            "latitude": lat,
            "longitude": lng,
            "daily": "temperature_2m_max",
            "forecast_days": FORECAST_DAYS,
            "timezone": timezone,
            "models": models_str,
            "temperature_unit": "celsius",
        }, timeout=20)
        r.raise_for_status()
        resp = r.json()

        # Check for rate limit error
        if resp.get("error"):
            reason = resp.get("reason", "")
            if "limit" in reason.lower():
                dprint(f"  [WARN] Ensemble API rate limited — falling back to deterministic only")
                _ensemble_rate_limited = True
                return {}
            dprint(f"  [WARN] Ensemble API error: {reason}")
            return {}

        data = resp.get("daily", {})
    except Exception as e:
        dprint(f"  [WARN] Ensemble API error: {e}")
        return {}

    dates = data.get("time", [])
    if not dates:
        return {}

    # Collect all member keys from all models
    member_keys = []
    for model_req, info in ENSEMBLE_MODELS.items():
        api_name = info["api_name"]
        max_members = info["members"]
        member_keys.append(f"temperature_2m_max_{api_name}")
        for m in range(1, max_members + 1):
            member_keys.append(f"temperature_2m_max_member{m:02d}_{api_name}")

    result = {}
    for i, date_str in enumerate(dates):
        members = []
        for key in member_keys:
            vals = data.get(key, [])
            if i < len(vals) and vals[i] is not None:
                members.append(vals[i])
        if members:
            result[date_str] = members

    return result


def _fetch_city(city):
    """Fetch both deterministic and ensemble for one city. Used by thread pool."""
    geo = CITY_GEO.get(city)
    if not geo:
        return city, {}, {}
    lat, lng, tz = geo[0], geo[1], geo[2]

    det = fetch_deterministic_forecasts(lat, lng, tz)
    ens = fetch_ensemble_forecasts(lat, lng, tz)

    return city, det, ens


# Progress callback — set by server.py to report scan progress
_progress_callback = None

def set_progress_callback(cb):
    global _progress_callback
    _progress_callback = cb


def fetch_all_city_forecasts(needed_cities):
    """Fetch deterministic + ensemble forecasts for all needed cities.
    Uses thread pool for concurrent requests (5 at a time).
    Returns: (city_det_forecasts, city_ens_forecasts)
    """
    global _ensemble_rate_limited
    _ensemble_rate_limited = False  # reset for each scan

    city_det_forecasts = {}
    city_ens_forecasts = {}
    sorted_cities = sorted(needed_cities)
    total = len(sorted_cities)
    done = 0

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_fetch_city, city): city for city in sorted_cities}

        for future in as_completed(futures):
            city, det, ens = future.result()
            city_det_forecasts[city] = det
            city_ens_forecasts[city] = ens
            done += 1

            det_models = len(next(iter(det.values()), {})) if det else 0
            ens_members = len(next(iter(ens.values()), [])) if ens else 0

            if _progress_callback:
                _progress_callback(done, total, city, det_models, ens_members)
            else:
                dprint(f"  {city:15s} | {det_models} det models | {ens_members} ens members | ({done}/{total})")

    return city_det_forecasts, city_ens_forecasts
