"""
Weather API module — fetches multi-model deterministic + ensemble forecasts from Open-Meteo.
"""
import time
import requests
import numpy as np
from config import (
    FORECAST_DAYS, DETERMINISTIC_MODELS, CITY_GEO,
    dprint, JSON_OUT,
)


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
        data = r.json().get("daily", {})
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
    """Fetch 51-member ECMWF ensemble for temperature_2m_max.
    Returns: {date_str: [temp_member00, ..., temp_member50], ...}
    """
    try:
        r = requests.get("https://ensemble-api.open-meteo.com/v1/ensemble", params={
            "latitude": lat,
            "longitude": lng,
            "daily": "temperature_2m_max",
            "forecast_days": FORECAST_DAYS,
            "timezone": timezone,
            "models": "ecmwf_ifs025_ensemble",
            "temperature_unit": "celsius",
        }, timeout=15)
        r.raise_for_status()
        data = r.json().get("daily", {})
    except Exception as e:
        dprint(f"  [WARN] Ensemble API error: {e}")
        return {}

    dates = data.get("time", [])
    if not dates:
        return {}

    result = {}
    for i, date_str in enumerate(dates):
        members = []
        for m in range(51):
            key = f"temperature_2m_max_member{m:02d}"
            vals = data.get(key, [])
            if i < len(vals) and vals[i] is not None:
                members.append(vals[i])
        if members:
            result[date_str] = members

    return result


def fetch_all_city_forecasts(needed_cities):
    """Fetch deterministic + ensemble forecasts for all needed cities.
    Returns: (city_det_forecasts, city_ens_forecasts)
    """
    city_det_forecasts = {}
    city_ens_forecasts = {}

    for city in sorted(needed_cities):
        geo = CITY_GEO.get(city)
        if not geo:
            continue
        lat, lng, tz = geo

        det = fetch_deterministic_forecasts(lat, lng, tz)
        city_det_forecasts[city] = det
        time.sleep(0.15)

        ens = fetch_ensemble_forecasts(lat, lng, tz)
        city_ens_forecasts[city] = ens
        time.sleep(0.15)

        if not JSON_OUT:
            det_dates = len(det)
            ens_dates = len(ens)
            det_models = len(next(iter(det.values()), {})) if det else 0
            ens_members = len(next(iter(ens.values()), [])) if ens else 0
            print(f"  {city:15s} | {det_models} det models x {det_dates}d | {ens_members} ens members x {ens_dates}d")

    return city_det_forecasts, city_ens_forecasts
