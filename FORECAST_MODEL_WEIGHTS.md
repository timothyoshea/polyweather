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

## Per-City Model Weights (Data-Driven)

Weights are derived from a **60-day verification study** (Jan 28 – Mar 29, 2026) using:
- **Open-Meteo Previous Runs API** — what each model forecasted 1 day ahead
- **Open-Meteo Archive API** — actual measured temperatures
- At exact **Polymarket ICAO station coordinates** (the same stations markets resolve against)

Methodology: `weight = 1 / MAE²`, normalized per city. Lower error = exponentially higher weight.

### ECMWF IFS — Best in 22/33 cities (avg MAE: 0.66°C)

Dominant model globally. Highest weights in:
- Paris (74%), Toronto (72%), Houston (71%), Chicago (69%)
- Warsaw (67%), Lucknow (66%), Buenos Aires (63%), London (63%)

### GFS — Best in 4 cities (avg MAE: 1.06°C)

Strongest on US West Coast:
- San Francisco (61%), Los Angeles (33%), Miami (26%), Shanghai (35%)

### ICON — Best in 1 city (avg MAE: 0.94°C)

- Singapore (36%) — also strong secondary in Milan (30%), Atlanta (29%)

### GEM — Best in 3 cities (avg MAE: 1.02°C)

Surprisingly strong in parts of Asia:
- Seoul (48%), Taipei (29%), Mexico City (25%)

### MeteoFrance — Best in 3 cities (avg MAE: 1.14°C)

Mediterranean and Middle East:
- Hong Kong (34%), Ankara (34%), Tel Aviv (30%)

## Full Weight Table

| City | ECMWF | GFS | ICON | GEM | MeteoFr | Best Model | MAE |
|------|-------|-----|------|-----|---------|------------|-----|
| Paris | **73.5%** | 7.3% | 6.8% | 6.1% | 6.3% | ECMWF | 0.17°C |
| London | **63.4%** | 11.2% | 6.6% | 9.6% | 9.2% | ECMWF | 0.21°C |
| Warsaw | **67.2%** | 7.9% | 8.4% | 7.9% | 8.6% | ECMWF | 0.24°C |
| Madrid | **58.9%** | 13.2% | 9.3% | 9.3% | 9.3% | ECMWF | 0.27°C |
| Houston | **70.6%** | 3.0% | 12.0% | 2.1% | 12.3% | ECMWF | 0.28°C |
| Lucknow | **66.1%** | 1.1% | 9.7% | 8.7% | 14.4% | ECMWF | 0.28°C |
| Chicago | **69.0%** | 4.7% | 13.3% | 4.7% | 8.3% | ECMWF | 0.36°C |
| Beijing | **58.6%** | 17.9% | 11.6% | 4.7% | 7.3% | ECMWF | 0.37°C |
| Munich | **48.3%** | 12.8% | 13.9% | 7.8% | 17.2% | ECMWF | 0.37°C |
| Wellington | **55.3%** | 14.2% | 15.9% | 5.3% | 9.3% | ECMWF | 0.37°C |
| Buenos Aires | **62.9%** | 2.2% | 11.2% | 13.2% | 10.5% | ECMWF | 0.38°C |
| Toronto | **71.6%** | 7.1% | 10.9% | 6.1% | 4.3% | ECMWF | 0.41°C |
| Austin | **56.0%** | 8.5% | 21.4% | 5.7% | 8.5% | ECMWF | 0.42°C |
| Denver | **42.0%** | 13.1% | 12.5% | 17.8% | 14.6% | ECMWF | 0.43°C |
| Dallas | **52.6%** | 6.8% | 10.2% | 13.7% | 16.6% | ECMWF | 0.45°C |
| Istanbul | **36.3%** | 9.1% | 22.3% | 12.2% | 20.2% | ECMWF | 0.47°C |
| Atlanta | **40.8%** | 9.0% | 28.9% | 13.0% | 8.2% | ECMWF | 0.48°C |
| Seattle | **36.7%** | 14.3% | 18.7% | 13.0% | 17.2% | ECMWF | 0.50°C |
| Sao Paulo | **45.7%** | 11.0% | 23.2% | 7.5% | 12.6% | ECMWF | 0.52°C |
| Milan | **30.2%** | 11.8% | **30.2%** | 7.4% | 20.4% | ECMWF/ICON | 0.55°C |
| Tokyo | **35.3%** | 20.5% | 15.9% | 20.0% | 8.3% | ECMWF | 0.67°C |
| NYC | **53.8%** | 9.3% | 13.3% | 9.8% | 13.8% | ECMWF | 0.73°C |
| Taipei | 17.0% | 17.0% | 20.9% | **29.0%** | 16.2% | GEM | 0.62°C |
| Mexico City | 19.3% | 24.4% | 7.3% | **25.2%** | 23.7% | GEM | 0.63°C |
| Seoul | 27.9% | 5.1% | 10.2% | **47.9%** | 9.0% | GEM | 1.26°C |
| Shanghai | 29.6% | **35.4%** | 8.7% | 22.6% | 3.8% | GFS | 0.64°C |
| Singapore | 9.9% | 21.3% | **36.2%** | 27.3% | 5.3% | ICON | 0.66°C |
| Miami | 12.9% | **25.6%** | 22.9% | 20.5% | 18.1% | GFS | 0.68°C |
| San Francisco | 27.2% | **61.2%** | 3.4% | 7.0% | 1.2% | GFS | 0.76°C |
| Hong Kong | 12.1% | 12.8% | 27.0% | 13.9% | **34.2%** | MeteoFrance | 0.79°C |
| Tel Aviv | 20.8% | 20.1% | 15.9% | 13.7% | **29.5%** | MeteoFrance | 0.47°C |
| Ankara | 25.9% | 15.6% | 12.3% | 12.3% | **34.0%** | MeteoFrance | 0.48°C |
| Los Angeles | 4.8% | **32.9%** | 31.8% | 18.4% | 12.0% | GFS | 1.22°C |

## Key Insights

1. **ECMWF dominates** — best in 22/33 cities, but NOT best everywhere
2. **Los Angeles & San Francisco** — ECMWF is worst here (3.18°C and 1.14°C MAE), GFS is far better. Likely due to complex coastal microclimate that US model handles better with higher-res terrain.
3. **Seoul** — All models struggle (1.26°C+ MAE), GEM surprisingly best. Korean peninsula weather is notoriously hard to forecast.
4. **Singapore** — ICON best, likely due to good tropical convection physics
5. **Mediterranean cities** (Tel Aviv, Ankara, Hong Kong) — MeteoFrance ARPEGE model excels, likely due to its Mediterranean climate tuning

## Uncertainty Calculation

The combined standard deviation uses a conservative approach — it takes the **larger** of:

- Ensemble standard deviation (spread across 122 members)
- Deterministic model spread / 3.5 (how much the 5 models disagree)

## Ensemble Composition

| Model | Members | Source |
|-------|---------|--------|
| ECMWF IFS 0.25 | 50 | European Centre for Medium-Range Weather Forecasts |
| NCEP GEFS | 31 | US National Centers for Environmental Prediction |
| ICON EPS | 39 | German Deutscher Wetterdienst |

## Implementation

- Per-city weights defined in `stats_agent.py` → `CITY_MODEL_WEIGHTS`
- Fallback weights in `DEFAULT_MODEL_WEIGHTS` for unverified cities
- `compute_combined_forecast(det_models, ensemble_members, city=None)` applies weights
- Weights normalized at runtime so missing models don't break calculation
- Verification methodology: 60-day window, day-1 forecast vs actual, `1/MAE²` weighting

## Data Sources

- Open-Meteo Previous Runs API: https://open-meteo.com/en/docs/previous-runs-api
- Open-Meteo Archive API: https://open-meteo.com/en/docs/historical-weather-api
- Verification period: 2026-01-28 to 2026-03-29 (60 days)
- Station coordinates: Polymarket ICAO weather stations (see `config.py`)
