"""
Output formatting — text and JSON display for trade opportunities.
"""
import json
from datetime import datetime
from config import (
    TOMORROW, TOMORROW_STR, TODAY, MAX_DATE, TIER1_ONLY,
    SURE_BET_MIN_PROB, SURE_BET_MIN_CONFIDENCE,
    EDGE_MIN_EDGE, EDGE_MIN_CONFIDENCE,
    SURE_BET_MAX_PRICE, SURE_BET_MIN_EDGE,
    EDGE_MAX_PRICE, EDGE_MIN_PROB,
    SAFE_NO_MIN_PROB, SAFE_NO_MIN_CONFIDENCE,
    MAX_MODEL_DISAGREEMENT, DEBUG,
    c_to_f,
)
from stats_agent import assess_risk


def polymarket_url(opp):
    event_slug = opp.get("event_slug", "")
    market_slug = opp.get("market_slug", "")
    condition_id = opp.get("condition_id", "")
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    if market_slug:
        return f"https://polymarket.com/market/{market_slug}"
    if condition_id:
        return f"https://polymarket.com/trade/{condition_id}"
    return "(no link)"


def print_opp(i, o, is_sure=False):
    side = o.get("side", "YES")
    if is_sure:
        tag = f"SURE {side}"
    elif o["edge"] >= 10:
        tag = "STRONG"
    else:
        tag = "EDGE"

    tier_label = {1: "Tier1", 2: "Tier2", 3: "Tier3"}[o["city_tier"]]
    conf_bar = "#" * (o["confidence"] // 10) + "." * (10 - o["confidence"] // 10)

    # Risk assessment
    risk = o.get("risk", "?")

    print(f"  {tag}  #{i}")
    print(f"  City         : {o['city']}  ({tier_label}, {o['horizon_days']}d out)")
    print(f"  Date         : {o['date']}")
    print(f"  Band         : {o['band_c']}  ({o['band_f']})")
    print(f"  Forecast     : {o['forecast_c']}C / {o['forecast_f']}F")
    print(f"  Eff. Std Dev : {o['effective_std']}C")
    print(f"  {side} Price  : {o['mkt_p']}c  ->  Model: {o['my_p']}%")
    if o.get("empirical_p") is not None:
        print(f"  Empirical    : {o['empirical_p']}% (from ensemble members)")
    print(f"  Edge         : +{o['edge']}pp")
    print(f"  EV/dollar    : {o.get('ev_per_dollar', 0):.3f}")
    print(f"  Half-Kelly   : {o['hk']}% of bankroll")
    print(f"  Confidence   : {o['confidence']}/100  [{conf_bar}]")
    print(f"  Risk         : {risk}")
    print(f"  Models       : {o['model_count']} det + {o['ensemble_count']} ensemble members")
    if o.get("ensemble_std"):
        print(f"  Ens. Std     : {o['ensemble_std']}C")
    if o.get("model_spread"):
        print(f"  Model Spread : {o['model_spread']}C")
    print()

    # Model consensus
    if o.get("model_values"):
        print(f"  MODEL CONSENSUS:")
        for model, temp in sorted(o["model_values"].items()):
            label = model.replace("_seamless", "").replace("ecmwf_ifs025", "ECMWF").upper()
            print(f"    {label:20s} : {temp:.1f}C / {c_to_f(temp):.0f}F")
        print()

    if is_sure:
        if side == "NO":
            print(f"  >>> TRADE: Buy NO on \"{o['question']}\"")
            print(f"      at {o['mkt_p']}c per share.")
            print()
            print(f"      WHY: {o['model_count']} models + {o['ensemble_count']}-member ensemble agree:")
            print(f"      forecast is {o['forecast_c']}C -- far from the {o['band_c']} band.")
            print(f"      {o['my_p']}% chance it stays OUTSIDE. NO at {o['mkt_p']}c = strong play.")
        else:
            print(f"  >>> TRADE: Buy YES on \"{o['question']}\"")
            print(f"      at {o['mkt_p']}c per share.")
            print()
            print(f"      WHY: {o['model_count']} models + {o['ensemble_count']}-member ensemble agree:")
            print(f"      forecast is {o['forecast_c']}C -- right in the {o['band_c']} band.")
            print(f"      {o['my_p']}% chance of YES. Priced at only {o['mkt_p']}c = strong play.")
    else:
        print(f"  >>> TRADE: Buy {side} on \"{o['question']}\"")
        print(f"      at {o['mkt_p']}c per share.")
        print()
        print(f"      WHY: Multi-model consensus {o['forecast_c']}C / {o['forecast_f']}F ->")
        print(f"      {o['my_p']}% {side} probability vs {o['mkt_p']}c market = +{o['edge']}pp edge.")
        if o["edge"] >= 10:
            print(f"      STRONG edge -- well above the {EDGE_MIN_EDGE*100:.0f}pp minimum.")

    print()
    print(f"  Link         : {polymarket_url(o)}")
    print(f"  Question     : {o['question']}")
    print()
    print(f"  -- API Data --")
    print(f"  Token ID     : {o['token_id']}")
    print(f"  Condition    : {o['condition_id']}")
    if o.get("event_slug"):
        print(f"  Event Slug   : {o['event_slug']}")
    print(f"  Price Src    : {o['price_src']}")
    print()
    print(f"  {'_' * 68}")
    print()


def print_results(opps):
    div = "=" * 76
    mode = f"TOMORROW ({TOMORROW_STR})" if TOMORROW else f"NEXT {(MAX_DATE - TODAY).days} DAYS"
    tier_note = " | TIER 1 ONLY" if TIER1_ONLY else ""

    print(div)
    print(f"  POLYWEATHER SCANNER v3.0  --  {mode}{tier_note}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  "
          f"Multi-model ensemble  |  Edge >={EDGE_MIN_EDGE*100:.0f}pp  |  "
          f"Confidence-weighted")
    print(div)

    if not opps:
        print(f"\n  No opportunities found for {mode}.")
        print("  The stricter v3 filters reduce false positives significantly.")
        print("  Run with --debug to see the full filter pipeline.\n")
        return

    sure_bets = [o for o in opps if o.get("bet_type") == "sure"]
    edge_bets = [o for o in opps if o.get("bet_type") == "edge"]
    safe_no_bets = [o for o in opps if o.get("bet_type") == "safe_no"]

    print(f"\n  {len(opps)} opportunit{'y' if len(opps) == 1 else 'ies'} "
          f"({len(sure_bets)} sure, {len(edge_bets)} edge, {len(safe_no_bets)} safe_no)\n")

    if sure_bets:
        print(f"  {'_' * 68}")
        print(f"  SURE BETS -- {len(sure_bets)} high-confidence plays "
              f"(>={SURE_BET_MIN_PROB*100:.0f}% prob, >={SURE_BET_MIN_CONFIDENCE} conf)")
        print(f"  {'_' * 68}\n")
        for i, o in enumerate(sure_bets, 1):
            print_opp(i, o, is_sure=True)

    if edge_bets:
        print(f"  {'_' * 68}")
        print(f"  EDGE BETS -- {len(edge_bets)} value plays "
              f"(>={EDGE_MIN_EDGE*100:.0f}pp edge, >={EDGE_MIN_CONFIDENCE} conf)")
        print(f"  {'_' * 68}\n")
        for i, o in enumerate(edge_bets, 1):
            print_opp(i, o, is_sure=False)

    if safe_no_bets:
        print(f"  {'_' * 68}")
        print(f"  SAFE NO BETS -- {len(safe_no_bets)} near-certain small-profit plays "
              f"(>={SAFE_NO_MIN_PROB*100:.0f}% prob, >={SAFE_NO_MIN_CONFIDENCE} conf)")
        print(f"  {'_' * 68}\n")
        for i, o in enumerate(safe_no_bets, 1):
            no_price = o['mkt_p'] / 100
            safe_return = (1 - no_price) / no_price * 100
            print(f"  SAFE_NO  #{i}")
            print(f"  City         : {o['city']}  (Tier{o['city_tier']}, {o['horizon_days']}d out)")
            print(f"  Date         : {o['date']}")
            print(f"  Band         : {o['band_c']}  ({o['band_f']})")
            print(f"  Forecast     : {o['forecast_c']}C / {o['forecast_f']}F")
            print(f"  NO Price     : {o['mkt_p']}c  ->  Model: {o['my_p']}%")
            print(f"  Return       : {safe_return:.1f}% (buy at {o['mkt_p']}c, collect $1)")
            print(f"  Confidence   : {o['confidence']}/100")
            print()
            print(f"  >>> TRADE: Buy NO on \"{o['question']}\"")
            print(f"      at {o['mkt_p']}c per share = {safe_return:.1f}% return")
            print()
            print(f"  Link         : {polymarket_url(o)}")
            print(f"  Token ID     : {o['token_id']}")
            print()
            print(f"  {'_' * 68}")
            print()

    print(div)
    best = opps[0]
    print(f"  SUMMARY: {len(opps)} trade{'s' if len(opps) != 1 else ''} "
          f"({len(sure_bets)} sure, {len(edge_bets)} edge) | "
          f"Best edge +{best['edge']}pp | Best confidence {best['confidence']}/100")
    print(div)
    print()


def print_json(opps):
    output = {
        "scanner_version": "3.0",
        "timestamp": datetime.now().isoformat(),
        "mode": "tomorrow" if TOMORROW else "multi-day",
        "tier1_only": TIER1_ONLY,
        "thresholds": {
            "sure_bet_min_prob": SURE_BET_MIN_PROB,
            "sure_bet_max_price": SURE_BET_MAX_PRICE,
            "sure_bet_min_edge": SURE_BET_MIN_EDGE,
            "sure_bet_min_confidence": SURE_BET_MIN_CONFIDENCE,
            "edge_min_prob": EDGE_MIN_PROB,
            "edge_max_price": EDGE_MAX_PRICE,
            "edge_min_edge": EDGE_MIN_EDGE,
            "edge_min_confidence": EDGE_MIN_CONFIDENCE,
            "max_model_disagreement": MAX_MODEL_DISAGREEMENT,
        },
        "opportunities": [],
    }
    for o in opps:
        entry = dict(o)
        if not DEBUG:
            entry.pop("model_values", None)
        entry["url"] = polymarket_url(o)
        output["opportunities"].append(entry)

    print(json.dumps(output, indent=2, default=str))
