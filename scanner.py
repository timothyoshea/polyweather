"""
PolyWeather Scanner v3.0 — Main entry point.
Multi-model ensemble forecasting with confidence-weighted trade signals.

Usage:
    python scanner.py                # scan next 5 days
    python scanner.py --tomorrow     # tomorrow only
    python scanner.py --debug        # verbose diagnostics
    python scanner.py --json         # JSON output
    python scanner.py --tier1-only   # only high-confidence cities

Dependencies:
    pip install -r requirements.txt
"""
import sys
import json
from datetime import datetime

from config import (
    DEBUG, TOMORROW, JSON_OUT, TIER1_ONLY,
    TODAY, TOMORROW_STR, MAX_DATE,
    CITY_GEO, MAX_MODEL_DISAGREEMENT,
    get_city_tier, dprint, c_to_f,
)
from weather_api import fetch_all_city_forecasts
from polymarket_api import (
    fetch_temperature_events,
    parse_event_title,
    parse_group_item,
    get_market_price,
)
from stats_agent import (
    compute_combined_forecast,
    effective_std,
    calc_probability,
    calc_probability_empirical,
    compute_confidence,
    evaluate_trade,
    check_model_agreement,
    half_kelly,
    TradeSignal,
    assess_risk,
    rank_opportunities,
)
from output import print_results, print_json


def scan():
    """Main scanning pipeline."""
    signals = []
    counters = {k: 0 for k in [
        "events", "markets", "parsed", "in_range", "forecast",
        "priced", "passed", "skipped_disagreement", "skipped_confidence",
        "skipped_empirical",
    ]}

    # 1. Fetch Polymarket events
    events = fetch_temperature_events()
    if not events:
        if not JSON_OUT:
            print("No temperature events found.")
        return []
    counters["events"] = len(events)

    # 2. Parse events to find needed cities
    needed_cities = set()
    event_data = []
    for e in events:
        parsed = parse_event_title(e.get("title", ""))
        if not parsed:
            continue
        city, date_key = parsed
        needed_cities.add(city)
        event_data.append((e, city, date_key))

    if not JSON_OUT:
        print(f"\nFetching multi-model forecasts for {len(needed_cities)} cities...")

    # 3. Fetch all forecasts
    city_det, city_ens = fetch_all_city_forecasts(needed_cities)

    if not JSON_OUT:
        print()
        target = f"tomorrow ({TOMORROW_STR})" if TOMORROW else f"next {(MAX_DATE - TODAY).days} days"
        print(f"Scanning for opportunities -- {target}...\n")

    # 4. Evaluate every market
    seen = set()

    for e, city, date_key in event_data:
        event_slug = e.get("slug", "")
        sub_markets = e.get("markets", [])

        is_fahrenheit = any("°F" in mk.get("groupItemTitle", "") for mk in sub_markets)

        det_models = city_det.get(city, {}).get(date_key, {})
        ens_members = city_ens.get(city, {}).get(date_key, [])

        forecast_info = compute_combined_forecast(det_models, ens_members)
        if forecast_info is None:
            dprint(f"SKIP no forecast data {city} {date_key}")
            continue

        forecast_temp = forecast_info["combined_forecast"]

        try:
            target_date = datetime.strptime(date_key, "%Y-%m-%d").date()
            horizon_days = (target_date - TODAY).days
        except Exception:
            horizon_days = 2

        eff_s = effective_std(forecast_info, horizon_days, city)
        confidence = compute_confidence(forecast_info, horizon_days, city)

        # Check model agreement
        if not check_model_agreement(forecast_info):
            spread = forecast_info.get("multi_model_spread", 0)
            dprint(f"SKIP model disagreement {spread:.1f}C > {MAX_MODEL_DISAGREEMENT}C for {city} {date_key}")
            counters["skipped_disagreement"] += 1
            continue

        if DEBUG:
            dprint(f"FORECAST {city} {date_key} (horizon={horizon_days}d, tier={get_city_tier(city)}):")
            dprint(f"  Combined: {forecast_temp:.1f}C, std={eff_s:.2f}C, confidence={confidence}")
            if forecast_info.get("ensemble_mean") is not None:
                dprint(f"  Ensemble: mean={forecast_info['ensemble_mean']:.1f}C, "
                       f"std={forecast_info['ensemble_std']:.2f}C, "
                       f"range=[{forecast_info['ensemble_min']:.1f}, {forecast_info['ensemble_max']:.1f}]")
            if forecast_info.get("model_values"):
                parts = [f"{m.replace('_seamless','').replace('ecmwf_ifs025','ecmwf')}={v:.1f}"
                         for m, v in forecast_info["model_values"].items()]
                dprint(f"  Models: {', '.join(parts)}")
                dprint(f"  Spread: {forecast_info.get('multi_model_spread', 0):.1f}C")

        for mk in sub_markets:
            counters["markets"] += 1
            group_title = mk.get("groupItemTitle", "")
            question = mk.get("question", "")

            parsed_band = parse_group_item(group_title, is_fahrenheit)
            if not parsed_band:
                dprint(f"SKIP unparseable band '{group_title}': {question[:50]}")
                continue
            lo, hi, band_type = parsed_band
            counters["parsed"] += 1

            key = f"{city}|{date_key}|{lo:.2f}|{band_type}"
            if key in seen:
                continue
            seen.add(key)
            counters["in_range"] += 1

            # Stats agent: calculate probabilities
            my_p = calc_probability(forecast_temp, eff_s, lo, hi, band_type)
            my_no_p = 1.0 - my_p
            emp_p = calc_probability_empirical(ens_members, lo, hi, band_type)
            emp_no_p = (1.0 - emp_p) if emp_p is not None else None
            counters["forecast"] += 1

            # Get prices
            yes_price, no_price, price_src = get_market_price(mk)
            if yes_price is None:
                dprint(f"SKIP no price {city} {date_key} {group_title}")
                continue
            counters["priced"] += 1

            # Token IDs
            ids_raw = mk.get("clobTokenIds", "[]")
            try:
                ids = json.loads(ids_raw) if isinstance(ids_raw, str) else ids_raw
            except Exception:
                ids = []
            yes_id = ids[0] if len(ids) > 0 else ""
            no_id = ids[1] if len(ids) > 1 else ""

            condition_id = mk.get("conditionId", "")
            market_slug = mk.get("slug", "")
            lo_f, hi_f = c_to_f(lo), c_to_f(hi)

            # Build display band
            if band_type == "below":
                band_c = f"<={lo:.0f}C"
                band_f = f"<={lo_f:.0f}F"
            elif band_type == "above":
                band_c = f">={lo:.0f}C"
                band_f = f">={lo_f:.0f}F"
            else:
                # Single-degree C bands (hi = lo+1) display as "16°C" to match Polymarket
                if abs(hi - lo - 1.0) < 0.01:
                    band_c = f"{lo:.0f}C"
                else:
                    band_c = f"{lo:.0f}-{hi:.0f}C"
                band_f = f"{lo_f:.0f}-{hi_f:.0f}F"

            dprint(f"CANDIDATE {city} {date_key} {band_c} "
                   f"fcst={forecast_temp:.1f}C YES={yes_price*100:.1f}c NO={no_price*100:.1f}c "
                   f"my_p={my_p*100:.1f}% conf={confidence}")

            # Stats agent: evaluate YES side
            bet_type, passes = evaluate_trade(
                "YES", my_p, yes_price, confidence, forecast_info,
                horizon_days, ens_members, lo, hi, band_type
            )
            if passes:
                counters["passed"] += 1
                sig = TradeSignal(
                    "YES", bet_type, my_p, yes_price, yes_id, city, date_key,
                    band_c, band_f, band_type, forecast_info, confidence,
                    horizon_days, eff_s, question, condition_id, market_slug,
                    event_slug, price_src, emp_p
                )
                signals.append(sig)

            # Stats agent: evaluate NO side
            bet_type, passes = evaluate_trade(
                "NO", my_no_p, no_price, confidence, forecast_info,
                horizon_days, ens_members, lo, hi, band_type
            )
            if passes:
                counters["passed"] += 1
                sig = TradeSignal(
                    "NO", bet_type, my_no_p, no_price, no_id, city, date_key,
                    band_c, band_f, band_type, forecast_info, confidence,
                    horizon_days, eff_s, question, condition_id, market_slug,
                    event_slug, price_src, emp_no_p
                )
                signals.append(sig)

    if DEBUG and not JSON_OUT:
        print("\n-- DIAGNOSTICS --")
        labels = list(counters.keys())
        descs = [
            "Temperature events", "Sub-markets", "Band parsed", "Unique bands",
            "Forecast matched", "Price available", "Passed all filters",
            "Skipped (model disagreement)", "Skipped (low confidence)",
            "Skipped (empirical mismatch)",
        ]
        for k, desc in zip(labels, descs):
            print(f"  {desc:35s}: {counters[k]}")
        print()

    # Stats agent: rank opportunities
    ranked = rank_opportunities(signals)

    # Convert to dicts with risk assessment
    opps = []
    for sig in ranked:
        d = sig.to_dict()
        d["risk"] = assess_risk(sig)
        opps.append(d)

    return opps


if __name__ == "__main__":
    if not JSON_OUT:
        print()
        print("  +========================================+")
        print("  |  PolyWeather Scanner v3.0              |")
        print("  |  Multi-Model Ensemble + Stats Agent    |")
        print("  +========================================+")
        flags = []
        if TOMORROW:   flags.append("TOMORROW")
        if DEBUG:      flags.append("DEBUG")
        if JSON_OUT:   flags.append("JSON")
        if TIER1_ONLY: flags.append("TIER1-ONLY")
        if flags:
            print(f"  [{' | '.join(flags)}]")
        print()

    try:
        opps = scan()
        if JSON_OUT:
            print_json(opps)
        else:
            print_results(opps)
    except KeyboardInterrupt:
        print("\n\nScan cancelled.")
        sys.exit(0)
