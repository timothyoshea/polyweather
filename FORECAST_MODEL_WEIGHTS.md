# Forecast Model Weighting System

## Overview

PolyWeather combines two data sources to produce temperature forecasts:

1. **Ensemble forecasts** (122 members) — 50 ECMWF + 31 GFS + 39 ICON
2. **Deterministic forecasts** (5 models) — ECMWF, GFS, ICON, GEM, MeteoFrance

The final forecast is a weighted blend:

```
forecast = 0.6 x ensemble_mean + 0.4 x weighted_deterministic_mean
```

The ensemble gets 60% weight because 122 members provide a more robust average. The deterministic models get 40% but are individually weighted by region based on historical accuracy.

## Regional Model Weights

Different weather models have different strengths depending on geography. The deterministic model blend uses region-specific weights rather than a simple average.

### Europe
*Cities: London, Paris, Madrid, Warsaw, Milan, Munich, Ankara, Istanbul, Moscow, Berlin, Vienna, Amsterdam, Stockholm*

| Model | Weight | Rationale |
|-------|--------|-----------|
| ECMWF IFS | 30% | Gold standard globally, strong European coverage |
| ICON | 28% | German DWD model, highest resolution in Europe |
| MeteoFrance | 22% | ARPEGE model, excels in Western Europe and Mediterranean |
| GFS | 12% | Decent but less tuned for Europe |
| GEM | 8% | Weakest in European domain |

### North America
*Cities: NYC, Chicago, Toronto, Dallas, Atlanta, Miami, Seattle, Austin, Denver, Houston, Los Angeles, San Francisco, Boston, Mexico City*

| Model | Weight | Rationale |
|-------|--------|-----------|
| GFS | 28% | US flagship model, best North American synoptic coverage |
| ECMWF IFS | 28% | Still excellent globally |
| GEM | 20% | Canadian model, strong for Toronto and northern US/Canada |
| ICON | 14% | Good but less tuned for NA |
| MeteoFrance | 10% | Weakest in NA domain |

### Asia
*Cities: Tokyo, Seoul, Shanghai, Beijing, Hong Kong, Taipei, Singapore, Chongqing, Chengdu, Wuhan, Shenzhen, Lucknow*

| Model | Weight | Rationale |
|-------|--------|-----------|
| ECMWF IFS | 40% | Dominant in Asia where other models have less station data |
| ICON | 20% | Reasonable global coverage |
| GFS | 20% | Decent but less verified in East/South Asia |
| GEM | 10% | Limited Asian focus |
| MeteoFrance | 10% | Limited Asian focus |

### South America
*Cities: Buenos Aires, Sao Paulo*

| Model | Weight | Rationale |
|-------|--------|-----------|
| ECMWF IFS | 35% | Best in tropics and subtropics |
| GFS | 25% | Good Southern Hemisphere coverage |
| ICON | 20% | Reasonable global model |
| GEM | 10% | Limited SA focus |
| MeteoFrance | 10% | Limited SA focus |

### Middle East
*Cities: Tel Aviv*

| Model | Weight | Rationale |
|-------|--------|-----------|
| ECMWF IFS | 40% | Strongest for arid/Mediterranean climates |
| ICON | 20% | Decent for Mediterranean basin |
| GFS | 15% | Adequate coverage |
| MeteoFrance | 15% | ARPEGE handles Mediterranean well |
| GEM | 10% | Weakest in this region |

### Oceania
*Cities: Wellington, Sydney, Melbourne*

| Model | Weight | Rationale |
|-------|--------|-----------|
| ECMWF IFS | 35% | Most reliable Southern Hemisphere model |
| GFS | 20% | Good global coverage |
| ICON | 20% | Reasonable but less verified here |
| GEM | 15% | Decent Southern Hemisphere performance |
| MeteoFrance | 10% | Limited Oceania focus |

## Uncertainty Calculation

The combined standard deviation uses a conservative approach — it takes the **larger** of:

- Ensemble standard deviation (spread across 122 members)
- Deterministic model spread / 3.5 (how much the 5 models disagree)

This ensures that if *either* source shows high uncertainty, it's reflected in the confidence score and position sizing.

## Ensemble Composition

The ensemble forecast averages 122 members from three model families:

| Model | Members | Source |
|-------|---------|--------|
| ECMWF IFS 0.25 | 50 | European Centre for Medium-Range Weather Forecasts |
| NCEP GEFS | 31 | US National Centers for Environmental Prediction |
| ICON EPS | 39 | German Deutscher Wetterdienst |

The ensemble mean is an unweighted average across all 122 members. ECMWF naturally dominates (41% of members) which aligns with its higher accuracy.

## Implementation

- Regional weights are defined in `stats_agent.py` in the `REGIONAL_MODEL_WEIGHTS` dict
- City-to-region mapping is in `CITY_REGION` dict
- The `compute_combined_forecast(det_models, ensemble_members, city=None)` function applies weights
- Weights are normalized at runtime so missing models don't break the calculation
- The applied weights and region are stored in `forecast_details.model_weights` and `forecast_details.region` on each opportunity

## Sources

- ECMWF verification reports: consistently ranked #1 in WMO global model intercomparisons
- DWD (ICON) verification: strongest short-range performance in European domain
- NCEP GFS: best-verified model for North American synoptic patterns
- Regional accuracy informed by WMO Lead Centre for Deterministic NWP Verification scores
