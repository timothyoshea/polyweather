"""
PolyWeather Configuration
All constants, thresholds, city data, and CLI flags in one place.
"""
import sys
from datetime import datetime, timedelta

# ── CLI Flags ────────────────────────────────────────────────────────────────

DEBUG      = "--debug"      in sys.argv
TOMORROW   = "--tomorrow"   in sys.argv
JSON_OUT   = "--json"       in sys.argv
TIER1_ONLY = "--tier1-only" in sys.argv

# ── Date Constants ───────────────────────────────────────────────────────────

FORECAST_DAYS = 6
TODAY         = datetime.now().date()
TODAY_STR     = TODAY.strftime("%Y-%m-%d")
TOMORROW_DATE = TODAY + timedelta(days=1)
TOMORROW_STR  = TOMORROW_DATE.strftime("%Y-%m-%d")
MAX_DATE      = TODAY + timedelta(days=5)

# ── Forecast Model MAE by Horizon (days out) ────────────────────────────────

HORIZON_MAE = {
    0: 1.0,   # same day
    1: 1.5,   # tomorrow
    2: 2.0,   # 2 days out
    3: 2.5,   # 3 days out
    4: 3.0,   # 4 days out
    5: 3.5,   # 5 days out
}

# ── City Tier System ─────────────────────────────────────────────────────────
# Tier 1: Very predictable (desert/Mediterranean/tropical) — trust forecasts more
# Tier 2: Moderately predictable — standard confidence
# Tier 3: Less predictable (mountain/continental/volatile) — add uncertainty buffer

# Based on ensemble spread analysis from research:
# Seoul, Miami, Tokyo, London have very low ensemble std (0.3-0.6C day-1)
TIER1_CITIES = {"Tel Aviv", "Singapore", "Miami", "Madrid", "Milan", "Lucknow", "Seoul", "Tokyo"}
# Chicago, Dallas degrade fast at day 3+; Wellington, Moscow are volatile
TIER3_CITIES = {"Wellington", "Ankara", "Moscow", "Chongqing", "Buenos Aires", "Chicago"}

TIER_MULTIPLIER = {1: 0.85, 2: 1.0, 3: 1.2}

# ── Trade Filtering Thresholds ───────────────────────────────────────────────

# Sure Bet criteria (near-certain plays)
SURE_BET_MIN_PROB       = 0.92    # >=92% model probability
SURE_BET_MAX_PRICE      = 0.08    # market price <=8c
SURE_BET_MIN_EDGE       = 0.02    # >=2pp edge
SURE_BET_MIN_CONFIDENCE = 70      # confidence score >=70

# Edge Bet criteria
EDGE_MIN_PROB       = 0.10
EDGE_MAX_PRICE      = 0.06
EDGE_MIN_EDGE       = 0.05        # >=5pp edge
EDGE_MIN_CONFIDENCE = 50

# Safe NO criteria (high probability, low return, near-certain)
SAFE_NO_MIN_PROB = 0.97       # >=97% NO probability
SAFE_NO_MAX_NO_PRICE = 0.97   # NO price <=97¢ (i.e., YES price >=3¢)
SAFE_NO_MIN_NO_PRICE = 0.90   # NO price >=90¢ (don't buy if too cheap — something's wrong)
SAFE_NO_MIN_RETURN = 0.02     # >=2% return (buy at 97¢, get $1 = 3.1% return)
SAFE_NO_MIN_CONFIDENCE = 60   # confidence >=60

# Anti-false-positive: skip if models disagree by more than this
MAX_MODEL_DISAGREEMENT = 3.0  # degrees C

# Liquidity / Position Sizing
BANKROLL_USD = 100                    # default bankroll for Kelly sizing
CLOB_API_BASE = "https://clob.polymarket.com"
CLOB_TIMEOUT = 5                      # seconds
LIQUIDITY_SAFETY_FACTOR = 0.4         # only use 40% of visible depth
MIN_EDGE_AFTER_SLIPPAGE = 0.03        # 3pp minimum edge after impact
MIN_LIQUIDITY_USD = 5.0               # skip if less than $5 fillable

# ── Deterministic Models to Query ────────────────────────────────────────────

DETERMINISTIC_MODELS = [
    "ecmwf_ifs025",
    "gfs_seamless",
    "icon_seamless",
    "gem_seamless",
    "meteofrance_seamless",
]

# ── City Geo Data (lat, lng, timezone) ───────────────────────────────────────

CITY_GEO = {
    "London":       ( 51.5074,  -0.1278,  "Europe/London"),
    "Paris":        ( 48.8566,   2.3522,  "Europe/Paris"),
    "Berlin":       ( 52.5200,  13.4050,  "Europe/Berlin"),
    "Madrid":       ( 40.4168,  -3.7038,  "Europe/Madrid"),
    "Warsaw":       ( 52.2297,  21.0122,  "Europe/Warsaw"),
    "Vienna":       ( 48.2082,  16.3738,  "Europe/Vienna"),
    "Amsterdam":    ( 52.3676,   4.9041,  "Europe/Amsterdam"),
    "Stockholm":    ( 59.3293,  18.0686,  "Europe/Stockholm"),
    "Milan":        ( 45.4642,   9.1900,  "Europe/Rome"),
    "Munich":       ( 48.1351,  11.5820,  "Europe/Berlin"),
    "Ankara":       ( 39.9334,  32.8597,  "Europe/Istanbul"),
    "Tel Aviv":     ( 32.0853,  34.7818,  "Asia/Jerusalem"),
    "New York":     ( 40.7128, -74.0060,  "America/New_York"),
    "NYC":          ( 40.7128, -74.0060,  "America/New_York"),
    "Chicago":      ( 41.8781, -87.6298,  "America/Chicago"),
    "Toronto":      ( 43.6532, -79.3832,  "America/Toronto"),
    "Dallas":       ( 32.7767, -96.7970,  "America/Chicago"),
    "Atlanta":      ( 33.7490, -84.3880,  "America/New_York"),
    "Boston":       ( 42.3601, -71.0589,  "America/New_York"),
    "Miami":        ( 25.7617, -80.1918,  "America/New_York"),
    "Seattle":      ( 47.6062,-122.3321,  "America/Los_Angeles"),
    "Sydney":       (-33.8688, 151.2093,  "Australia/Sydney"),
    "Melbourne":    (-37.8136, 144.9631,  "Australia/Melbourne"),
    "Wellington":   (-41.2865, 174.7762,  "Pacific/Auckland"),
    "Tokyo":        ( 35.6762, 139.6503,  "Asia/Tokyo"),
    "Seoul":        ( 37.5665, 126.9780,  "Asia/Seoul"),
    "Shanghai":     ( 31.2304, 121.4737,  "Asia/Shanghai"),
    "Beijing":      ( 39.9042, 116.4074,  "Asia/Shanghai"),
    "Hong Kong":    ( 22.3193, 114.1694,  "Asia/Hong_Kong"),
    "Taipei":       ( 25.0330, 121.5654,  "Asia/Taipei"),
    "Singapore":    (  1.3521, 103.8198,  "Asia/Singapore"),
    "Chongqing":    ( 29.4316, 106.9123,  "Asia/Shanghai"),
    "Chengdu":      ( 30.5728, 104.0668,  "Asia/Shanghai"),
    "Wuhan":        ( 30.5928, 114.3055,  "Asia/Shanghai"),
    "Shenzhen":     ( 22.5431, 114.0579,  "Asia/Shanghai"),
    "Lucknow":      ( 26.8467,  80.9462,  "Asia/Kolkata"),
    "Buenos Aires": (-34.6037, -58.3816,  "America/Argentina/Buenos_Aires"),
    "Sao Paulo":    (-23.5505, -46.6333,  "America/Sao_Paulo"),
    "Moscow":       ( 55.7558,  37.6173,  "Europe/Moscow"),
    "Cape Town":    (-33.9249,  18.4241,  "Africa/Johannesburg"),
}

CITY_NORMALIZE = {
    "nyc": "NYC",
    "new york city": "NYC",
    "new york": "NYC",
    "sao paulo": "Sao Paulo",
    "são paulo": "Sao Paulo",
    "tel aviv": "Tel Aviv",
    "hong kong": "Hong Kong",
    "buenos aires": "Buenos Aires",
    "cape town": "Cape Town",
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def dprint(*args):
    if DEBUG:
        print("  [DBG]", *args)

def get_city_tier(city):
    if city in TIER1_CITIES:
        return 1
    if city in TIER3_CITIES:
        return 3
    return 2

def normalize_city(raw):
    stripped = raw.strip()
    lower = stripped.lower()
    if lower in CITY_NORMALIZE:
        return CITY_NORMALIZE[lower]
    if stripped in CITY_GEO:
        return stripped
    titled = stripped.title()
    if titled in CITY_GEO:
        return titled
    return stripped

def c_to_f(c):
    return c * 9 / 5 + 32
