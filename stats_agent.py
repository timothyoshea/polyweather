"""
Stats Agent — The brain of PolyWeather.

Handles all statistical modelling for trade decisions:
- Multi-model forecast combination (ensemble + deterministic)
- Probability calculation with calibrated uncertainty
- Confidence scoring (model agreement, ensemble spread, horizon, city tier)
- Trade signal generation (sure bets vs edge bets)
- Kelly criterion position sizing
- Expected value calculations
- Risk assessment and false-positive filtering
"""
import numpy as np
from scipy import stats
from config import (
    HORIZON_MAE, TIER_MULTIPLIER, MAX_MODEL_DISAGREEMENT,
    SURE_BET_MIN_PROB, SURE_BET_MAX_PRICE, SURE_BET_MIN_EDGE, SURE_BET_MIN_CONFIDENCE,
    EDGE_MIN_PROB, EDGE_MAX_PRICE, EDGE_MIN_EDGE, EDGE_MIN_CONFIDENCE,
    SAFE_NO_MIN_PROB, SAFE_NO_MAX_NO_PRICE, SAFE_NO_MIN_NO_PRICE,
    SAFE_NO_MIN_RETURN, SAFE_NO_MIN_CONFIDENCE,
    get_city_tier, dprint,
)


# ══════════════════════════════════════════════════════════════════════════════
# FORECAST COMBINATION
# ══════════════════════════════════════════════════════════════════════════════

def compute_combined_forecast(det_models, ensemble_members):
    """Combine deterministic model forecasts and ensemble members into a
    unified forecast with uncertainty estimate.

    Args:
        det_models: dict of {model_name: temp_celsius} from deterministic models
        ensemble_members: list of temperatures from ECMWF ensemble members

    Returns:
        dict with forecast statistics, or None if insufficient data.
        Keys: combined_forecast, combined_std, ensemble_mean, ensemble_std,
              multi_model_mean, multi_model_spread, model_values, etc.
    """
    has_ensemble = ensemble_members and len(ensemble_members) >= 10
    has_det = det_models and len(det_models) >= 2

    if not has_ensemble and not has_det:
        return None

    result = {}

    # Ensemble statistics
    if has_ensemble:
        arr = np.array(ensemble_members)
        result["ensemble_mean"] = float(np.mean(arr))
        result["ensemble_std"] = float(np.std(arr, ddof=1))
        result["ensemble_min"] = float(np.min(arr))
        result["ensemble_max"] = float(np.max(arr))
        result["ensemble_p10"] = float(np.percentile(arr, 10))
        result["ensemble_p25"] = float(np.percentile(arr, 25))
        result["ensemble_p50"] = float(np.median(arr))
        result["ensemble_p75"] = float(np.percentile(arr, 75))
        result["ensemble_p90"] = float(np.percentile(arr, 90))
        result["ensemble_count"] = len(ensemble_members)
        result["ensemble_iqr"] = result["ensemble_p75"] - result["ensemble_p25"]
    else:
        result["ensemble_mean"] = None
        result["ensemble_std"] = None
        result["ensemble_count"] = 0

    # Deterministic model statistics
    if has_det:
        vals = list(det_models.values())
        arr_d = np.array(vals)
        result["multi_model_mean"] = float(np.mean(arr_d))
        result["multi_model_std"] = float(np.std(arr_d, ddof=1)) if len(vals) > 1 else 1.5
        result["multi_model_spread"] = float(np.max(arr_d) - np.min(arr_d))
        result["multi_model_min"] = float(np.min(arr_d))
        result["multi_model_max"] = float(np.max(arr_d))
        result["model_count"] = len(vals)
        result["model_values"] = dict(det_models)
    else:
        result["multi_model_mean"] = None
        result["multi_model_spread"] = None
        result["multi_model_std"] = None
        result["model_count"] = 0
        result["model_values"] = {}

    # Combined forecast: weighted blend
    # Ensemble gets more weight because 51 members > 5 deterministic models
    if has_ensemble and has_det:
        result["combined_forecast"] = 0.6 * result["ensemble_mean"] + 0.4 * result["multi_model_mean"]
        # Use the LARGER uncertainty — conservative approach
        result["combined_std"] = max(
            result["ensemble_std"],
            result["multi_model_spread"] / 3.5,
        )
    elif has_ensemble:
        result["combined_forecast"] = result["ensemble_mean"]
        result["combined_std"] = result["ensemble_std"]
    else:
        result["combined_forecast"] = result["multi_model_mean"]
        result["combined_std"] = max(result["multi_model_spread"] / 2.5, 1.5)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# EFFECTIVE UNCERTAINTY
# ══════════════════════════════════════════════════════════════════════════════

def effective_std(forecast_info, horizon_days, city):
    """Compute the effective standard deviation for probability calculations.

    Combines three sources of uncertainty:
    1. Horizon-based MAE (longer forecasts = more uncertainty)
    2. Ensemble-derived spread (model's own uncertainty estimate)
    3. City tier multiplier (some cities are harder to forecast)

    We take the MAXIMUM of horizon MAE and ensemble std — this is conservative
    and prevents overconfident probabilities that lead to bad trades.
    """
    horizon_mae = HORIZON_MAE.get(min(horizon_days, 5), 3.5)
    tier = get_city_tier(city)
    tier_mult = TIER_MULTIPLIER[tier]

    combined_std = forecast_info.get("combined_std", 2.0)

    # Use the larger of horizon-based MAE and ensemble-derived uncertainty
    base_std = max(horizon_mae, combined_std)

    return base_std * tier_mult


# ══════════════════════════════════════════════════════════════════════════════
# PROBABILITY CALCULATION
# ══════════════════════════════════════════════════════════════════════════════

def calc_probability(forecast_mean, eff_std, lo, hi, band_type):
    """Compute probability that actual temperature falls in the given band.

    Uses normal distribution CDF with the forecast as mean and the
    effective standard deviation as the uncertainty parameter.

    Args:
        forecast_mean: combined forecast temperature (Celsius)
        eff_std: effective standard deviation (Celsius)
        lo, hi: band boundaries (Celsius)
        band_type: 'below', 'above', or 'exact'

    Returns:
        probability (0.0 to 1.0)
    """
    if band_type == "below":
        # "X or below" — probability of temp <= X (inclusive, so +1 for the bucket)
        return stats.norm.cdf(lo + 1, forecast_mean, eff_std)
    elif band_type == "above":
        # "X or higher" — probability of temp >= X
        return 1.0 - stats.norm.cdf(lo, forecast_mean, eff_std)
    else:
        # Exact band [lo, hi) — probability of temp in range
        return stats.norm.cdf(hi, forecast_mean, eff_std) - stats.norm.cdf(lo, forecast_mean, eff_std)


def calc_probability_empirical(ensemble_members, lo, hi, band_type):
    """Calculate probability directly from ensemble members (non-parametric).
    This is a cross-check against the normal distribution assumption.

    Returns probability or None if insufficient members.
    """
    if not ensemble_members or len(ensemble_members) < 20:
        return None

    arr = np.array(ensemble_members)
    n = len(arr)

    if band_type == "below":
        count = np.sum(arr <= lo + 1)
    elif band_type == "above":
        count = np.sum(arr >= lo)
    else:
        count = np.sum((arr >= lo) & (arr < hi))

    return float(count) / n


# ══════════════════════════════════════════════════════════════════════════════
# CONFIDENCE SCORING
# ══════════════════════════════════════════════════════════════════════════════

def compute_confidence(forecast_info, horizon_days, city):
    """Compute a 0-100 confidence score for a forecast.

    Components:
    - Model agreement (0-30): How close are the 5 deterministic models?
    - Ensemble tightness (0-30): How tight is the 51-member ensemble?
    - Forecast horizon (0-20): Closer dates = more reliable
    - City tier (0-20): Predictable climates score higher

    Higher confidence = more trustworthy trade signal.
    """
    score = 0

    # ── Model agreement (0-30) ──
    spread = forecast_info.get("multi_model_spread")
    if spread is not None:
        if spread <= 1.0:
            score += 30
        elif spread <= 2.0:
            score += 22
        elif spread <= 3.0:
            score += 14
        elif spread <= 4.0:
            score += 7

    # ── Ensemble confidence (0-30) ──
    ens_std = forecast_info.get("ensemble_std")
    if ens_std is not None:
        if ens_std <= 1.0:
            score += 30
        elif ens_std <= 1.5:
            score += 24
        elif ens_std <= 2.0:
            score += 18
        elif ens_std <= 2.5:
            score += 12
        elif ens_std <= 3.0:
            score += 6

    # ── Forecast horizon (0-20) ──
    horizon_scores = {0: 20, 1: 18, 2: 15, 3: 10, 4: 7, 5: 5}
    score += horizon_scores.get(min(horizon_days, 5), 5)

    # ── City tier (0-20) ──
    tier = get_city_tier(city)
    tier_scores = {1: 20, 2: 15, 3: 10}
    score += tier_scores[tier]

    return min(score, 100)


# ══════════════════════════════════════════════════════════════════════════════
# KELLY CRITERION POSITION SIZING
# ══════════════════════════════════════════════════════════════════════════════

def half_kelly(p, price):
    """Half-Kelly criterion for position sizing.

    Full Kelly maximizes long-term growth but is too aggressive.
    Half-Kelly gives ~75% of optimal growth with much less variance.

    Args:
        p: our estimated probability of winning
        price: cost per share (market price)

    Returns:
        fraction of bankroll to bet (0.0 to ~0.5)
    """
    if price >= 1.0 or price <= 0:
        return 0
    b = (1 - price) / price  # payout ratio
    kelly = (b * p - (1 - p)) / b
    return max(0, kelly / 2)  # half kelly


def expected_value(prob, price):
    """Expected value per dollar wagered.
    EV = (prob * payout) - cost
    For binary markets: payout = 1.0 if correct, cost = price
    """
    if price <= 0 or price >= 1:
        return 0
    return prob * (1.0 / price - 1.0) - (1 - prob)


# ══════════════════════════════════════════════════════════════════════════════
# TRADE SIGNAL GENERATION
# ══════════════════════════════════════════════════════════════════════════════

class TradeSignal:
    """Represents a potential trade opportunity with all analysis data."""

    def __init__(self, side, bet_type, prob, price, token_id, city, date_key,
                 band_c, band_f, band_type, forecast_info, confidence,
                 horizon_days, eff_std, question, condition_id, market_slug,
                 event_slug, price_src, empirical_prob=None):
        self.side = side
        self.bet_type = bet_type
        self.prob = prob
        self.price = price
        self.token_id = token_id
        self.city = city
        self.date = date_key
        self.band_c = band_c
        self.band_f = band_f
        self.band_type = band_type
        self.forecast_info = forecast_info
        self.confidence = confidence
        self.horizon_days = horizon_days
        self.effective_std = eff_std
        self.question = question
        self.condition_id = condition_id
        self.market_slug = market_slug
        self.event_slug = event_slug
        self.price_src = price_src
        self.empirical_prob = empirical_prob

        self.edge = prob - price
        self.hk = half_kelly(prob, price)
        self.ev = expected_value(prob, price)

    def to_dict(self):
        fc = self.forecast_info
        return {
            "side": self.side,
            "bet_type": self.bet_type,
            "city": self.city,
            "date": self.date,
            "band_c": self.band_c,
            "band_f": self.band_f,
            "band_type": self.band_type,
            "forecast_c": round(fc["combined_forecast"], 1),
            "forecast_f": round(fc["combined_forecast"] * 9 / 5 + 32, 1),
            "my_p": round(self.prob * 100, 1),
            "empirical_p": round(self.empirical_prob * 100, 1) if self.empirical_prob is not None else None,
            "mkt_p": round(self.price * 100, 1),
            "edge": round(self.edge * 100, 1),
            "ev_per_dollar": round(self.ev, 3),
            "hk": round(self.hk * 100, 1),
            "confidence": self.confidence,
            "city_tier": get_city_tier(self.city),
            "horizon_days": self.horizon_days,
            "effective_std": round(self.effective_std, 2),
            "ensemble_std": round(fc.get("ensemble_std") or 0, 2),
            "model_spread": round(fc.get("multi_model_spread") or 0, 2),
            "model_count": fc.get("model_count", 0),
            "ensemble_count": fc.get("ensemble_count", 0),
            "model_values": fc.get("model_values", {}),
            "question": self.question,
            "token_id": self.token_id,
            "condition_id": self.condition_id,
            "market_slug": self.market_slug,
            "event_slug": self.event_slug,
            "price_src": self.price_src,
        }


def evaluate_trade(side, prob, price, confidence, forecast_info, horizon_days,
                   ensemble_members=None, lo=None, hi=None, band_type=None):
    """Evaluate whether a potential trade meets our criteria.

    Returns: (bet_type, passes) where bet_type is 'sure'/'edge'/None
    and passes is True/False.

    Logic:
    1. Check sure bet criteria first (highest bar)
    2. Then check edge bet criteria
    3. Apply anti-false-positive filters
    """
    # Anti-false-positive: check parametric vs empirical agreement
    if ensemble_members and len(ensemble_members) >= 20:
        emp_yes_p = calc_probability_empirical(ensemble_members, lo, hi, band_type)
        if emp_yes_p is not None:
            # For NO side, compare against 1 - empirical_yes
            emp_p = emp_yes_p if side == "YES" else (1.0 - emp_yes_p)
            # If empirical and parametric disagree by >15pp, skip
            if abs(prob - emp_p) > 0.15:
                dprint(f"  -> SKIP {side}: parametric ({prob:.1%}) vs empirical ({emp_p:.1%}) disagree by >{15}pp")
                return None, False

    # Sure bet check
    if 0 < price <= SURE_BET_MAX_PRICE:
        if prob >= SURE_BET_MIN_PROB and (prob - price) >= SURE_BET_MIN_EDGE:
            if confidence >= SURE_BET_MIN_CONFIDENCE:
                return "sure", True
            else:
                dprint(f"  -> SKIP SURE {side}: confidence {confidence} < {SURE_BET_MIN_CONFIDENCE}")
                return None, False

    # Edge bet check
    if 0 < price <= EDGE_MAX_PRICE:
        edge = prob - price
        if prob >= EDGE_MIN_PROB and edge >= EDGE_MIN_EDGE:
            if confidence >= EDGE_MIN_CONFIDENCE:
                return "edge", True
            else:
                dprint(f"  -> SKIP EDGE {side}: confidence {confidence} < {EDGE_MIN_CONFIDENCE}")
                return None, False

    return None, False


def check_model_agreement(forecast_info):
    """Check if models agree enough to trade.
    Returns True if models are in sufficient agreement.
    """
    spread = forecast_info.get("multi_model_spread")
    if spread is not None and spread > MAX_MODEL_DISAGREEMENT:
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
# RISK ASSESSMENT
# ══════════════════════════════════════════════════════════════════════════════

def assess_risk(signal):
    """Classify risk level of a trade signal.
    Returns: 'LOW', 'MEDIUM', or 'HIGH'
    """
    risk_score = 0

    # Higher horizon = more risk
    if signal.horizon_days >= 4:
        risk_score += 2
    elif signal.horizon_days >= 2:
        risk_score += 1

    # Lower confidence = more risk
    if signal.confidence < 60:
        risk_score += 2
    elif signal.confidence < 75:
        risk_score += 1

    # High ensemble spread = more risk
    ens_std = signal.forecast_info.get("ensemble_std", 0)
    if ens_std and ens_std > 2.5:
        risk_score += 2
    elif ens_std and ens_std > 1.5:
        risk_score += 1

    # Empirical/parametric disagreement = more risk
    if signal.empirical_prob is not None:
        disagree = abs(signal.prob - signal.empirical_prob)
        if disagree > 0.10:
            risk_score += 2
        elif disagree > 0.05:
            risk_score += 1

    if risk_score <= 2:
        return "LOW"
    elif risk_score <= 4:
        return "MEDIUM"
    else:
        return "HIGH"


def rank_opportunities(signals):
    """Rank trade signals by quality.
    Sure bets first, then by a composite score of edge, confidence, and EV.
    """
    def sort_key(s):
        type_rank = 0 if s.bet_type == "sure" else 1
        # Composite: 40% confidence, 30% edge, 30% EV
        composite = 0.4 * (s.confidence / 100) + 0.3 * min(s.edge / 0.2, 1.0) + 0.3 * min(s.ev / 2.0, 1.0)
        return (type_rank, -composite)

    return sorted(signals, key=sort_key)
