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

# ── City Geo Data — EXACT AIRPORT STATION COORDINATES ────────────────────────
# Polymarket resolves temperatures against specific ICAO weather stations
# (usually major airports). Using city-centre coords can be 1-2°C off.
# ICAO codes confirmed from Polymarket market description fields (Wunderground URLs).
# Format: (lat, lng, timezone, icao_code)

CITY_GEO = {
    # ── Europe (confirmed from Polymarket) ──
    "London":       ( 51.5053,   0.0553, "Europe/London",     "EGLC"),  # London City Airport
    "Paris":        ( 49.0097,   2.5478, "Europe/Paris",      "LFPG"),  # Charles de Gaulle
    "Madrid":       ( 40.4722,  -3.5611, "Europe/Madrid",     "LEMD"),  # Barajas
    "Warsaw":       ( 52.1657,  20.9671, "Europe/Warsaw",     "EPWA"),  # Chopin Airport
    "Milan":        ( 45.6306,   8.7231, "Europe/Rome",       "LIMC"),  # Malpensa
    "Munich":       ( 48.3538,  11.7861, "Europe/Berlin",     "EDDM"),  # Munich Airport
    "Ankara":       ( 40.1281,  32.9951, "Europe/Istanbul",   "LTAC"),  # Esenboga
    # ── Europe (inferred — no active Polymarket market yet) ──
    "Berlin":       ( 52.3514,  13.4939, "Europe/Berlin",     "EDDB"),  # Brandenburg
    "Vienna":       ( 48.1103,  16.5697, "Europe/Vienna",     "LOWW"),  # Vienna Intl
    "Amsterdam":    ( 52.3086,   4.7639, "Europe/Amsterdam",  "EHAM"),  # Schiphol
    "Stockholm":    ( 59.6519,  17.9186, "Europe/Stockholm",  "ESSA"),  # Arlanda

    # ── Middle East (confirmed) ──
    "Tel Aviv":     ( 32.0114,  34.8867, "Asia/Jerusalem",    "LLBG"),  # Ben Gurion (NOAA source)

    # ── North America (confirmed) ──
    "New York":     ( 40.7772, -73.8726, "America/New_York",  "KLGA"),  # LaGuardia
    "NYC":          ( 40.7772, -73.8726, "America/New_York",  "KLGA"),  # LaGuardia
    "Chicago":      ( 41.9742, -87.9073, "America/Chicago",   "KORD"),  # O'Hare
    "Toronto":      ( 43.6772, -79.6306, "America/Toronto",   "CYYZ"),  # Pearson
    "Dallas":       ( 32.8471, -96.8518, "America/Chicago",   "KDAL"),  # Love Field
    "Atlanta":      ( 33.6407, -84.4277, "America/New_York",  "KATL"),  # Hartsfield-Jackson
    "Miami":        ( 25.7959, -80.2870, "America/New_York",  "KMIA"),  # Miami Intl
    "Seattle":      ( 47.4502,-122.3088, "America/Los_Angeles","KSEA"), # Sea-Tac
    # ── North America (inferred) ──
    "Boston":       ( 42.3656, -71.0096, "America/New_York",  "KBOS"),  # Logan
    # ── North America (new cities found in active markets) ──
    "Austin":       ( 30.1975, -97.6664, "America/Chicago",   "KAUS"),  # Bergstrom
    "Denver":       ( 39.8561,-104.6737, "America/Denver",    "KDEN"),  # Denver Intl
    "Houston":      ( 29.6454, -95.2789, "America/Chicago",   "KHOU"),  # Hobby
    "Los Angeles":  ( 33.9425,-118.4081, "America/Los_Angeles","KLAX"), # LAX
    "San Francisco":( 37.6213,-122.3790, "America/Los_Angeles","KSFO"), # SFO

    # ── Asia-Pacific (confirmed) ──
    "Tokyo":        ( 35.5494, 139.7798, "Asia/Tokyo",        "RJTT"),  # Haneda
    "Seoul":        ( 37.4602, 126.4407, "Asia/Seoul",        "RKSI"),  # Incheon
    "Shanghai":     ( 31.1443, 121.8083, "Asia/Shanghai",     "ZSPD"),  # Pudong
    "Beijing":      ( 40.0799, 116.6031, "Asia/Shanghai",     "ZBAA"),  # Capital Intl
    "Hong Kong":    ( 22.3080, 113.9185, "Asia/Hong_Kong",    "VHHH"),  # HK Intl (HK Observatory source)
    "Taipei":       ( 25.0777, 121.2325, "Asia/Taipei",       "RCTP"),  # Taoyuan (NOAA source)
    "Singapore":    (  1.3502, 103.9944, "Asia/Singapore",    "WSSS"),  # Changi
    "Chongqing":    ( 29.7192, 106.6414, "Asia/Shanghai",     "ZUCK"),  # Jiangbei
    "Chengdu":      ( 30.5785, 103.9471, "Asia/Shanghai",     "ZUUU"),  # Shuangliu
    "Wuhan":        ( 30.7838, 114.2081, "Asia/Shanghai",     "ZHHH"),  # Tianhe
    "Shenzhen":     ( 22.6393, 113.8107, "Asia/Shanghai",     "ZGSZ"),  # Bao'an
    "Lucknow":      ( 26.7606,  80.8893, "Asia/Kolkata",      "VILK"),  # Chaudhary Charan Singh

    # ── Oceania (confirmed) ──
    "Wellington":   (-41.3272, 174.8053, "Pacific/Auckland",  "NZWN"),  # Wellington Intl
    # ── Oceania (inferred) ──
    "Sydney":       (-33.9461, 151.1772, "Australia/Sydney",  "YSSY"),  # Kingsford Smith
    "Melbourne":    (-37.6733, 144.8433, "Australia/Melbourne","YMML"), # Tullamarine

    # ── South America (confirmed) ──
    "Buenos Aires": (-34.8222, -58.5358, "America/Argentina/Buenos_Aires", "SAEZ"),  # Ezeiza
    "Sao Paulo":    (-23.4356, -46.4731, "America/Sao_Paulo", "SBGR"),  # Guarulhos

    # ── Other (inferred) ──
    "Moscow":       ( 55.9726,  37.4146, "Europe/Moscow",     "UUEE"),  # Sheremetyevo
    "Cape Town":    (-33.9649,  18.6017, "Africa/Johannesburg","FACT"), # Cape Town Intl
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
    "los angeles": "Los Angeles",
    "san francisco": "San Francisco",
}

# ── ICAO Station Names ───────────────────────────────────────────────────────

ICAO_STATION_NAME = {
    "EGLC": "London City Airport",
    "LFPG": "Charles de Gaulle Airport",
    "LEMD": "Madrid-Barajas Airport",
    "EPWA": "Warsaw Chopin Airport",
    "LIMC": "Milan Malpensa Airport",
    "EDDM": "Munich Airport",
    "LTAC": "Ankara Esenboga Airport",
    "EDDB": "Berlin Brandenburg Airport",
    "LOWW": "Vienna International Airport",
    "EHAM": "Amsterdam Schiphol Airport",
    "ESSA": "Stockholm Arlanda Airport",
    "LLBG": "Ben Gurion Airport",
    "KLGA": "LaGuardia Airport",
    "KORD": "Chicago O'Hare Airport",
    "CYYZ": "Toronto Pearson Airport",
    "KDAL": "Dallas Love Field",
    "KATL": "Atlanta Hartsfield-Jackson",
    "KMIA": "Miami International Airport",
    "KSEA": "Seattle-Tacoma Airport",
    "KBOS": "Boston Logan Airport",
    "KAUS": "Austin-Bergstrom Airport",
    "KDEN": "Denver International Airport",
    "KHOU": "Houston Hobby Airport",
    "KLAX": "Los Angeles LAX",
    "KSFO": "San Francisco SFO",
    "RJTT": "Tokyo Haneda Airport",
    "RKSI": "Seoul Incheon Airport",
    "ZSPD": "Shanghai Pudong Airport",
    "ZBAA": "Beijing Capital Airport",
    "VHHH": "Hong Kong International Airport",
    "RCTP": "Taipei Taoyuan Airport",
    "WSSS": "Singapore Changi Airport",
    "ZUCK": "Chongqing Jiangbei Airport",
    "ZUUU": "Chengdu Shuangliu Airport",
    "ZHHH": "Wuhan Tianhe Airport",
    "ZGSZ": "Shenzhen Bao'an Airport",
    "VILK": "Lucknow Airport",
    "NZWN": "Wellington Airport",
    "YSSY": "Sydney Kingsford Smith",
    "YMML": "Melbourne Tullamarine",
    "SAEZ": "Buenos Aires Ezeiza Airport",
    "SBGR": "Sao Paulo Guarulhos Airport",
    "UUEE": "Moscow Sheremetyevo Airport",
    "FACT": "Cape Town International Airport",
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
