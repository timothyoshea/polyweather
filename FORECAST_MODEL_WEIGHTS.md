# Forecast Model Weighting System

## Overview

PolyWeather combines two data sources to produce temperature forecasts:

1. **Ensemble forecasts** (122 members) — 50 ECMWF + 31 GFS + 39 ICON
2. **Deterministic forecasts** (5 models) — ECMWF, GFS, ICON, GEM, MeteoFrance

The final forecast is a weighted blend:

```
forecast = 0.6 × ensemble_mean + 0.4 × weighted_deterministic_mean
```

The ensemble gets 60% weight because 122 members provide a more robust average. The deterministic models get 40% but are individually weighted **per city** based on verified historical accuracy.

## Verification Methodology

- **Period**: January 2024 – March 2026 (786+ days per city)
- **Source**: Open-Meteo Previous Runs API (day-1 forecasts) vs Archive API (actual measurements)
- **Coordinates**: Exact Polymarket ICAO weather station locations (the same stations markets resolve against)
- **Metric**: Mean Absolute Error (MAE) on daily maximum temperature
- **Weight formula**: `1 / MAE²`, normalized per city — a model with half the error gets 4× the weight

## Overall Model Ranking

| Rank | Model | Avg MAE | Best in # Cities |
|------|-------|---------|-----------------|
| 1 | **ECMWF IFS** | 0.78°C | **29 / 38** |
| 2 | ICON | 0.97°C | 2 |
| 3 | GEM | 1.10°C | 1 |
| 4 | GFS | 1.16°C | 2 |
| 5 | MeteoFrance | 1.20°C | 4 |

## Per-City Weights and MAE

### Europe

| City | ECMWF | GFS | ICON | GEM | MeteoFr | Best | MAE | n |
|------|-------|-----|------|-----|---------|------|-----|---|
| London | **60.0%** | 9.7% | 12.2% | 10.3% | 7.8% | ECMWF | 0.27°C | 786 |
| Paris | **51.6%** | 10.4% | 16.2% | 15.7% | 6.1% | ECMWF | 0.38°C | 786 |
| Madrid | **62.4%** | 11.6% | 13.5% | 7.1% | 5.5% | ECMWF | 0.32°C | 786 |
| Warsaw | **39.0%** | 10.3% | 21.5% | 16.4% | 12.8% | ECMWF | 0.47°C | 786 |
| Munich | **51.8%** | 15.5% | 13.1% | 12.1% | 7.4% | ECMWF | 0.49°C | 786 |
| Milan | **33.0%** | 18.3% | 25.6% | 13.0% | 10.0% | ECMWF | 0.70°C | 786 |
| Moscow | **56.1%** | 7.6% | 11.1% | 14.0% | 11.2% | ECMWF | 0.38°C | 786 |
| Istanbul | 22.1% | 9.9% | 23.2% | 17.0% | **27.7%** | MeteoFr | 0.66°C | 819 |
| Ankara | 22.6% | 15.6% | 16.3% | 21.1% | **24.4%** | MeteoFr | 0.67°C | 819 |

### Middle East

| City | ECMWF | GFS | ICON | GEM | MeteoFr | Best | MAE | n |
|------|-------|-----|------|-----|---------|------|-----|---|
| Tel Aviv | 6.4% | 26.2% | **30.9%** | 23.3% | 13.3% | ICON | 0.65°C | 819 |

### North America

| City | ECMWF | GFS | ICON | GEM | MeteoFr | Best | MAE | n |
|------|-------|-----|------|-----|---------|------|-----|---|
| Houston | **50.5%** | 9.0% | 15.2% | 7.4% | 17.9% | ECMWF | 0.64°C | 787 |
| Chicago | **44.1%** | 11.9% | 22.0% | 7.4% | 14.5% | ECMWF | 0.58°C | 787 |
| Toronto | **38.1%** | 16.5% | 19.0% | 13.9% | 12.5% | ECMWF | 0.63°C | 787 |
| Dallas | **39.1%** | 11.7% | 18.2% | 16.0% | 15.0% | ECMWF | 0.72°C | 787 |
| Austin | **38.4%** | 15.0% | 22.4% | 14.0% | 10.1% | ECMWF | 0.71°C | 787 |
| Denver | **35.9%** | 14.7% | 17.4% | 18.9% | 13.1% | ECMWF | 0.68°C | 787 |
| Atlanta | **32.7%** | 13.3% | 24.9% | 17.7% | 11.4% | ECMWF | 0.72°C | 787 |
| Seattle | **26.7%** | 19.0% | 18.0% | 17.6% | 18.6% | ECMWF | 0.82°C | 787 |
| NYC | **24.6%** | 17.7% | 22.7% | 19.1% | 16.0% | ECMWF | 0.92°C | 787 |
| Mexico City | **26.4%** | 12.5% | 18.8% | 19.1% | 23.2% | ECMWF | 0.92°C | 787 |
| Miami | 22.0% | 19.5% | **26.0%** | 10.4% | 22.0% | ICON | 0.73°C | 819 |
| San Francisco | 29.7% | **41.8%** | 15.3% | 11.1% | 2.1% | GFS | 1.07°C | 819 |
| Los Angeles | 4.4% | **35.0%** | 21.8% | 22.2% | 16.6% | GFS | 1.19°C | 819 |

### Asia

| City | ECMWF | GFS | ICON | GEM | MeteoFr | Best | MAE | n |
|------|-------|-----|------|-----|---------|------|-----|---|
| Chongqing | **59.3%** | 11.0% | 13.3% | 7.9% | 8.6% | ECMWF | 0.54°C | 786 |
| Wuhan | **52.1%** | 9.8% | 13.8% | 13.7% | 10.7% | ECMWF | 0.48°C | 786 |
| Lucknow | **51.1%** | 3.7% | 15.1% | 8.9% | 21.2% | ECMWF | 0.60°C | 786 |
| Beijing | **50.0%** | 7.9% | 16.6% | 9.7% | 15.8% | ECMWF | 0.54°C | 786 |
| Chengdu | **46.7%** | 11.5% | 15.9% | 12.5% | 13.3% | ECMWF | 0.61°C | 786 |
| Shanghai | **34.7%** | 23.6% | 19.0% | 18.4% | 4.2% | ECMWF | 0.67°C | 786 |
| Taipei | **28.7%** | 14.6% | 15.3% | 28.5% | 12.9% | ECMWF | 0.80°C | 786 |
| Tokyo | **28.2%** | 18.0% | 26.4% | 13.2% | 14.2% | ECMWF | 0.84°C | 786 |
| Singapore | **25.6%** | 18.3% | 21.0% | 21.4% | 13.6% | ECMWF | 0.80°C | 786 |
| Seoul | 24.2% | 3.7% | 29.8% | **32.6%** | 9.7% | GEM | 1.23°C | 819 |
| Hong Kong | 20.5% | 20.4% | 7.5% | 12.1% | **39.5%** | MeteoFr | 0.72°C | 819 |
| Shenzhen | 17.5% | 13.3% | 11.9% | 26.6% | **30.8%** | MeteoFr | 0.93°C | 819 |

### South America

| City | ECMWF | GFS | ICON | GEM | MeteoFr | Best | MAE | n |
|------|-------|-----|------|-----|---------|------|-----|---|
| Buenos Aires | **47.1%** | 6.3% | 19.8% | 9.8% | 17.0% | ECMWF | 0.55°C | 787 |
| Sao Paulo | **49.2%** | 10.4% | 20.7% | 9.2% | 10.5% | ECMWF | 0.62°C | 787 |

### Oceania

| City | ECMWF | GFS | ICON | GEM | MeteoFr | Best | MAE | n |
|------|-------|-----|------|-----|---------|------|-----|---|
| Wellington | **45.9%** | 17.7% | 15.9% | 6.1% | 14.4% | ECMWF | 0.36°C | 786 |

## Key Insights

1. **ECMWF dominates** — best in 29/38 cities over 2+ years of data
2. **Los Angeles is ECMWF's worst city** — 3.34°C MAE vs GFS at 1.19°C. Complex coastal microclimate that the US model handles far better with higher-res terrain data
3. **San Francisco** — Same pattern as LA, GFS wins with 41.8% weight
4. **Seoul** — Hardest city to forecast (1.23°C best MAE). GEM surprisingly best, possibly due to Korean peninsula terrain handling
5. **Istanbul & Ankara** — MeteoFrance ARPEGE wins, likely due to Mediterranean climate tuning
6. **Hong Kong & Shenzhen** — MeteoFrance best in subtropical South China, unexpected
7. **Tel Aviv & Miami** — ICON wins, good tropical/subtropical physics
8. **NYC** — Most contested major city (only 4% gap to 2nd), all models contribute meaningfully

## Uncertainty Calculation

The combined standard deviation uses a conservative approach — takes the **larger** of:
- Ensemble standard deviation (spread across 122 members)
- Deterministic model spread / 3.5 (how much the 5 models disagree)

## Ensemble Composition

| Model | Members | Source |
|-------|---------|--------|
| ECMWF IFS 0.25 | 50 | European Centre for Medium-Range Weather Forecasts |
| NCEP GEFS | 31 | US National Centers for Environmental Prediction |
| ICON EPS | 39 | German Deutscher Wetterdienst |

## Implementation

- Per-city weights: `stats_agent.py` → `CITY_MODEL_WEIGHTS`
- Fallback: `DEFAULT_MODEL_WEIGHTS` for unverified cities (ECMWF-heavy)
- Function: `compute_combined_forecast(det_models, ensemble_members, city=None)`
- Weights normalized at runtime so missing models don't break the calculation

## Data Sources

- Open-Meteo Previous Runs API: https://open-meteo.com/en/docs/previous-runs-api
- Open-Meteo Archive API: https://open-meteo.com/en/docs/historical-weather-api
- Verification period: 2024-01-01 to 2026-03-29 (786+ days per city)
- Station coordinates: Polymarket ICAO weather stations (see `config.py`)
